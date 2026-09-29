# -*- coding: utf-8 -*-
"""Safe optional Qwen post-processing for already anonymised text.

The model never receives originals or decoder values. Placeholder tokens are
replaced with opaque sentinels before inference and restored only after strict
validation. Missing packages/model files always produce a no-op fallback.
"""

from __future__ import annotations

from dataclasses import dataclass
from difflib import SequenceMatcher
import os
import platform
import re
import sys
import threading
from typing import Any, Callable, Mapping, Optional


DEFAULT_MODEL_ID = "Qwen/Qwen3.5-0.8B"
ALLOWED_OPERATIONS = frozenset({"grammar", "whitespace", "punctuation", "formatting"})
PLACEHOLDER_RE = re.compile(
    r"(?:\{\{[^{}\n]{1,120}\}\}|\[\[[^\[\]\n]{1,120}\]\]|"
    r"/(?=[A-Za-zА-Яа-яЁё][A-Za-zА-Яа-яЁё0-9 _.:\-]{0,119}/)"
    r"[A-Za-zА-Яа-яЁё][A-Za-zА-Яа-яЁё0-9 _.:\-]{0,119}/|"
    r"\[(?=[A-Za-zА-Яа-яЁё][A-Za-zА-Яа-яЁё0-9 _.:/\-]{0,119}\])"
    r"[A-Za-zА-Яа-яЁё][A-Za-zА-Яа-яЁё0-9 _.:/\-]{0,119}\])"
)
_FACT_NUMBER_RE = re.compile(r"\d+(?:[.,:/-]\d+)*")
_MODALITY_RE = re.compile(r"\b(?:не|нет|нельзя|можно|может|могут|могу|можем|мог\w*|долж\w*|обязан\w*|вправе|запрещ\w*|разреш\w*|must|shall|may|can|cannot|not|should)\b", re.I)
_SENTINEL_RE = re.compile(r"<<QWENPH\d+>>|\ue000QWENPH\d+\ue001")


def _as_bool(value: Any, default: bool = False) -> bool:
    """Parse persisted/UI boolean values without treating ``"false"`` as true."""

    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        normalized = value.strip().casefold()
        if normalized in {"1", "true", "yes", "y", "on"}:
            return True
        if normalized in {"", "0", "false", "no", "n", "off"}:
            return False
        return default
    if value is None:
        return default
    return bool(value)


class PlaceholderValidationError(ValueError):
    pass


class QwenGenerationCancelled(RuntimeError):
    """Raised internally when the caller cancels a generation."""


@dataclass(frozen=True)
class QwenPostprocessorSettings:
    enabled: bool = False
    model_id: str = DEFAULT_MODEL_ID
    device: str = "auto"
    max_new_tokens: int = 512
    temperature: float = 0.0
    # Local-only is the safe runtime default.  A network download must happen
    # through qwen_offline.install_model after explicit user consent.
    local_files_only: bool = True
    model_path: str = ""
    allow_network_download: bool = False
    minimum_similarity: float = 0.72

    @classmethod
    def from_mapping(cls, raw: Optional[Mapping[str, Any]]) -> "QwenPostprocessorSettings":
        if isinstance(raw, cls):
            return raw
        raw = raw if isinstance(raw, Mapping) else {}
        try:
            max_new_tokens = max(1, min(int(raw.get("max_new_tokens", 512)), 4096))
        except (TypeError, ValueError):
            max_new_tokens = 512
        try:
            temperature = max(0.0, min(float(raw.get("temperature", 0.0)), 1.0))
        except (TypeError, ValueError):
            temperature = 0.0
        try:
            minimum_similarity = max(0.6, min(float(raw.get("minimum_similarity", 0.72)), 1.0))
        except (TypeError, ValueError):
            minimum_similarity = 0.72
        model_id = str(raw.get("model_id", DEFAULT_MODEL_ID) or "").strip()
        from pathlib import Path
        if model_id and model_id != DEFAULT_MODEL_ID and not Path(model_id).expanduser().is_absolute():
            raise ValueError("Arbitrary remote Qwen model IDs are not permitted")
        return cls(
            enabled=_as_bool(raw.get("enabled", False)),
            model_id=model_id,
            device=str(raw.get("device", "auto") or "auto").strip().lower(),
            max_new_tokens=max_new_tokens,
            temperature=temperature,
            local_files_only=_as_bool(raw.get("local_files_only", True), default=True),
            model_path=str(raw.get("model_path", "") or "").strip(),
            allow_network_download=_as_bool(raw.get("allow_network_download", False)),
            minimum_similarity=minimum_similarity,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "enabled": self.enabled,
            "model_id": self.model_id,
            "device": self.device,
            "max_new_tokens": self.max_new_tokens,
            "temperature": self.temperature,
            "local_files_only": self.local_files_only,
            "model_path": self.model_path,
            "allow_network_download": self.allow_network_download,
            "minimum_similarity": self.minimum_similarity,
        }


@dataclass(frozen=True)
class ProtectedText:
    text: str
    tokens: tuple[str, ...]


def extract_placeholders(text: str) -> tuple[str, ...]:
    return tuple(match.group(0) for match in PLACEHOLDER_RE.finditer(text or ""))


def protect_placeholders(text: str) -> ProtectedText:
    tokens: list[str] = []

    def replace(match: re.Match[str]) -> str:
        index = len(tokens)
        tokens.append(match.group(0))
        return f"<<QWENPH{index}>>"

    return ProtectedText(PLACEHOLDER_RE.sub(replace, text or ""), tuple(tokens))


def restore_and_validate(generated: str, protected: ProtectedText, *, minimum_similarity: float = 0.72) -> str:
    expected_chevron = tuple(f"<<QWENPH{i}>>" for i in range(len(protected.tokens)))
    expected_pua = tuple(f"\ue000QWENPH{i}\ue001" for i in range(len(protected.tokens)))
    found_sentinels = tuple(_SENTINEL_RE.findall(generated or ""))
    if found_sentinels != expected_chevron and found_sentinels != expected_pua:
        raise PlaceholderValidationError("model changed placeholder sentinels")
    if extract_placeholders(generated):
        raise PlaceholderValidationError("model invented a placeholder")
    if _FACT_NUMBER_RE.findall(generated) != _FACT_NUMBER_RE.findall(protected.text):
        raise PlaceholderValidationError("model changed factual numbers")
    if _MODALITY_RE.findall(generated.casefold()) != _MODALITY_RE.findall(protected.text.casefold()):
        raise PlaceholderValidationError("model changed modality or negation")
    original_length = max(len(protected.text), 1)
    if not 0.65 <= len(generated) / original_length <= 1.35:
        raise PlaceholderValidationError("model changed too much text")
    if SequenceMatcher(None, protected.text, generated).ratio() < minimum_similarity:
        raise PlaceholderValidationError("rewrite exceeds conservative edit scope")

    result = generated
    for index, token in enumerate(protected.tokens):
        result = result.replace(f"<<QWENPH{index}>>", token).replace(f"\ue000QWENPH{index}\ue001", token)
    if extract_placeholders(result) != protected.tokens:
        raise PlaceholderValidationError("restored placeholder sequence differs")
    return result


def choose_device(requested: str = "auto", *, torch_module: Any = None) -> str:
    requested = (requested or "auto").lower()
    if requested not in {"auto", "cpu", "mps", "cuda"} or requested == "cpu":
        return "cpu"
    if torch_module is None:
        try:
            import torch as torch_module  # type: ignore
        except Exception:
            return "cpu"
    if requested in {"auto", "mps"} and platform.system() == "Darwin":
        if platform.machine() in {"arm64", "aarch64"}:
            try:
                if torch_module.backends.mps.is_available():
                    return "mps"
            except Exception:
                pass
        return "cpu"
    if requested in {"auto", "cuda"} and platform.system() == "Windows":
        try:
            if torch_module.cuda.is_available():
                return "cuda"
        except Exception:
            pass
    return "cpu"


def _verified_local_model(settings: QwenPostprocessorSettings) -> str:
    from pathlib import Path
    from qwen_offline import get_default_model_dir, validate_model
    candidate = settings.model_path
    if settings.model_id and settings.model_id != DEFAULT_MODEL_ID and not Path(settings.model_id).expanduser().is_absolute():
        raise ValueError("Qwen requires an absolute local model directory")
    if not candidate:
        # A remote repository name is never an inference source. Settings may
        # select an explicit absolute local path, whose bytes must match pins.
        configured = Path(settings.model_id).expanduser()
        candidate = str(configured) if configured.is_absolute() else str(get_default_model_dir())
    root = Path(candidate).expanduser()
    def has_symlink_component(path: Any) -> bool:
        current = Path(path.anchor) if path.anchor else Path()
        for component in path.parts[1:] if path.is_absolute() else path.parts:
            current /= component
            if current.is_symlink():
                return True
        return False
    # Do not follow a user-controlled symlink for model roots.  ``resolve``
    # below is useful for a canonical loader path, but must happen only after
    # this lstat-style check; otherwise a link could escape the model store.
    if not root.is_absolute() or has_symlink_component(root) or not root.is_dir():
        raise ValueError("Qwen requires an absolute local model directory")
    if not validate_model(root, verify_hash=True):
        raise ValueError("Qwen model failed pinned SHA-256 verification")
    return str(root.resolve())


def _default_loader(settings: QwenPostprocessorSettings) -> Any:
    """Load the official multimodal model for text-only chat, lazily."""
    if sys.version_info[:2] != (3, 11):
        raise RuntimeError("Qwen требует Python 3.11")
    model_ref = _verified_local_model(settings)
    try:
        import intel_torch_compat
        intel_torch_compat.apply_intel_torch_compat()
    except Exception:
        pass
    import torch  # type: ignore
    from transformers import AutoProcessor  # type: ignore
    try:
        from transformers import AutoModelForImageTextToText  # type: ignore
    except ImportError:  # transformers versions following the model card name it this way
        from transformers import AutoModelForMultimodalLM as AutoModelForImageTextToText  # type: ignore

    # Do not allow a seemingly accidental first-use network fetch.  The
    # installer is the only code path that should download the official model.
    local_only = True
    device = choose_device(settings.device, torch_module=torch)
    try:
        from qwen_offline import estimate_resources
        estimate = estimate_resources(cpu_only=device == "cpu")
        if not estimate.suitable:
            raise MemoryError("доступной памяти недостаточно для запуска Qwen")
    except ImportError:
        pass
    processor = AutoProcessor.from_pretrained(
        model_ref, local_files_only=local_only,
        trust_remote_code=False,
    )
    dtype = torch.float16 if device in {"mps", "cuda"} else torch.float32
    model = AutoModelForImageTextToText.from_pretrained(
        model_ref,
        local_files_only=local_only,
        trust_remote_code=False,
        torch_dtype=dtype,
    ).to(device)
    model.eval()

    class TransformersBackend:
        def generate(self, prompt: str, *, max_new_tokens: int, temperature: float,
                     cancel_check: Optional[Callable[[], bool]] = None) -> str:
            if cancel_check and cancel_check():
                raise QwenGenerationCancelled("Qwen generation cancelled")
            messages = [{"role": "user", "content": [{"type": "text", "text": prompt}]}]
            inputs = processor.apply_chat_template(
                messages,
                add_generation_prompt=True,
                tokenize=True,
                return_dict=True,
                return_tensors="pt",
            )
            inputs = {key: value.to(device) for key, value in inputs.items()}
            kwargs = {
                "max_new_tokens": max_new_tokens,
                "do_sample": temperature > 0,
                "pad_token_id": getattr(processor.tokenizer, "eos_token_id", None),
            }
            if temperature > 0:
                kwargs["temperature"] = temperature
            if cancel_check:
                try:
                    from transformers import StoppingCriteria, StoppingCriteriaList  # type: ignore

                    class _CancelCriteria(StoppingCriteria):
                        def __call__(self, input_ids, scores, **kwargs):
                            return bool(cancel_check())

                    kwargs["stopping_criteria"] = StoppingCriteriaList([_CancelCriteria()])
                except ImportError:
                    pass
            with torch.inference_mode():
                output = model.generate(**inputs, **kwargs)
            prompt_length = inputs["input_ids"].shape[-1]
            if cancel_check and cancel_check():
                raise QwenGenerationCancelled("Qwen generation cancelled")
            return processor.decode(output[0][prompt_length:], skip_special_tokens=True)

    return TransformersBackend()


def runtime_preflight(settings: QwenPostprocessorSettings | Mapping[str, Any] | None = None) -> dict[str, Any]:
    """Return an honest, no-load Qwen readiness status for UI and tests."""

    parsed = settings if isinstance(settings, QwenPostprocessorSettings) else QwenPostprocessorSettings.from_mapping(settings)
    reasons = []
    if sys.version_info[:2] != (3, 11):
        reasons.append("Требуется Python 3.11")
    model_ref = parsed.model_path or parsed.model_id
    try:
        model_ref = _verified_local_model(parsed)
    except Exception:
        reasons.append("Локальная модель отсутствует или не прошла проверку SHA-256")
    return {
        "ready": not reasons,
        "python": platform.python_version(),
        "model_ref": model_ref,
        "reasons": reasons,
    }



class QwenPlaceholderPostprocessor:
    def __init__(
        self,
        settings: Optional[QwenPostprocessorSettings | Mapping[str, Any]] = None,
        *,
        model_loader: Optional[Callable[[QwenPostprocessorSettings], Any]] = None,
    ) -> None:
        self.settings = settings if isinstance(settings, QwenPostprocessorSettings) else QwenPostprocessorSettings.from_mapping(settings)
        self._model_loader = model_loader or _default_loader
        self._backend: Any = None
        self._load_attempted = False
        self._lock = threading.RLock()
        self.last_status = "disabled" if not self.settings.enabled else "not_loaded"

    @property
    def loaded(self) -> bool:
        return self._backend is not None

    def reset(self, settings: Optional[QwenPostprocessorSettings | Mapping[str, Any]] = None) -> None:
        with self._lock:
            if settings is not None:
                self.settings = settings if isinstance(settings, QwenPostprocessorSettings) else QwenPostprocessorSettings.from_mapping(settings)
            self._backend = None
            self._load_attempted = False
            self.last_status = "disabled" if not self.settings.enabled else "not_loaded"

    def _ensure_loaded(self) -> Any:
        with self._lock:
            if self._backend is not None:
                return self._backend
            if self._load_attempted:
                return None
            self._load_attempted = True
            try:
                self._backend = self._model_loader(self.settings)
                self.last_status = "loaded" if self._backend is not None else "unavailable"
            except Exception as exc:
                self.last_status = f"unavailable: {type(exc).__name__}"
                self._backend = None
            return self._backend

    @staticmethod
    def _prompt(text: str) -> str:
        return (
            "Ты — консервативный редактор русских юридических документов. "
            "Текст уже обезличен. Сохрани каждый служебный токен вида <<QWENPH0>>, <<QWENPH1>> побайтно, "
            "в том же порядке и количестве, без изменений. Не восстанавливай персональные сведения, "
            "не меняй цифры и даты, не придумывай факты. Исправляй только согласование, пробелы, "
            "пунктуацию и локальные опечатки. Верни только исправленный текст без вступительных и заключительных комментариев.\n\n"
            f"ДОКУМЕНТ:\n{text}"
        )

    def process(self, anonymized_text: str, *, cancel_check: Optional[Callable[[], bool]] = None) -> str:
        original = anonymized_text if isinstance(anonymized_text, str) else str(anonymized_text or "")
        if not self.settings.enabled:
            self.last_status = "disabled"
            return original
        if cancel_check and cancel_check():
            self.last_status = "cancelled"
            return original
        protected = protect_placeholders(original)
        if not protected.tokens:
            self.last_status = "no_placeholders"
            return original
        backend = self._ensure_loaded()
        if backend is None:
            return original
        try:
            prompt = self._prompt(protected.text)
            try:
                generated = backend.generate(
                    prompt,
                    max_new_tokens=self.settings.max_new_tokens,
                    temperature=self.settings.temperature,
                    cancel_check=cancel_check,
                )
            except TypeError as exc:
                # Preserve compatibility with third-party/test backends that
                # predate cancellation, while never hiding their other errors.
                if "cancel_check" not in str(exc):
                    raise
                generated = backend.generate(
                    prompt,
                    max_new_tokens=self.settings.max_new_tokens,
                    temperature=self.settings.temperature,
                )
            if cancel_check and cancel_check():
                self.last_status = "cancelled"
                return original
            result = restore_and_validate(
                str(generated).strip(), protected,
                minimum_similarity=self.settings.minimum_similarity,
            )
            self.last_status = "ok"
            return result
        except QwenGenerationCancelled:
            self.last_status = "cancelled"
            return original
        except Exception as exc:
            self.last_status = f"rejected: {type(exc).__name__}"
            return original

    postprocess = process
    postprocess_text = process


__all__ = [
    "ALLOWED_OPERATIONS", "DEFAULT_MODEL_ID", "PLACEHOLDER_RE",
    "PlaceholderValidationError", "QwenGenerationCancelled", "ProtectedText",
    "QwenPlaceholderPostprocessor", "QwenPostprocessorSettings",
    "choose_device", "extract_placeholders", "protect_placeholders",
    "restore_and_validate", "runtime_preflight",
]

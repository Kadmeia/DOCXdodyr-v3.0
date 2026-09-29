# -*- coding: utf-8 -*-
import pytest

from qwen_postprocessor import (
    QwenPlaceholderPostprocessor,
    QwenGenerationCancelled,
    QwenPostprocessorSettings,
    _verified_local_model,
    choose_device,
    extract_placeholders,
)


def test_settings_parse_string_booleans_from_ui_or_legacy_json():
    disabled = QwenPostprocessorSettings.from_mapping(
        {"enabled": "false", "local_files_only": "0"}
    )
    assert disabled.enabled is False
    assert disabled.local_files_only is False

    enabled = QwenPostprocessorSettings.from_mapping(
        {"enabled": "yes", "local_files_only": "true"}
    )
    assert enabled.enabled is True
    assert enabled.local_files_only is True


class FakeModel:
    def __init__(self, transform):
        self.transform = transform
        self.calls = []

    def generate(self, prompt, *, max_new_tokens, temperature):
        self.calls.append((prompt, max_new_tokens, temperature))
        return self.transform(prompt)


@pytest.mark.parametrize("version", [(3, 10, 0), (3, 12, 0)])
def test_qwen_rejects_unsupported_python_before_loading_model(monkeypatch, version):
    import qwen_postprocessor as module

    monkeypatch.setattr(module.sys, "version_info", version)
    monkeypatch.setattr(module, "_verified_local_model", lambda _settings: "synthetic-local-model")
    assert "Требуется Python 3.11" in module.runtime_preflight()["reasons"]
    with pytest.raises(RuntimeError, match="Python 3.11"):
        module._default_loader(QwenPostprocessorSettings())


def _document(prompt):
    return prompt.split("ДОКУМЕНТ:\n", 1)[1]


def test_disabled_is_noop_and_lazy():
    loaded = []
    processor = QwenPlaceholderPostprocessor(
        QwenPostprocessorSettings(enabled=False),
        model_loader=lambda settings: loaded.append(settings),
    )
    text = "Уважаемый [ФИО_1], направляем [Наименование_1]."
    assert processor.process(text) == text
    assert loaded == []


def test_fake_model_edits_grammar_and_preserves_placeholders():
    loaded = []
    fake = FakeModel(lambda prompt: _document(prompt).replace("направляем", "направляем Вам"))

    def loader(settings):
        loaded.append(settings)
        return fake

    processor = QwenPlaceholderPostprocessor(
        {"enabled": True, "max_new_tokens": 42},
        model_loader=loader,
    )
    text = "Уважаемый [ФИО_1], направляем [Наименование_1]."
    result = processor.process(text)
    assert result == "Уважаемый [ФИО_1], направляем Вам [Наименование_1]."
    assert extract_placeholders(result) == ("[ФИО_1]", "[Наименование_1]")
    assert loaded and fake.calls[0][1:] == (42, 0.0)


@pytest.mark.parametrize(
    "bad_output",
    [
        "Уважаемый \ue000QWENPH0\ue001, документ готов.",
        "Уважаемый \ue000QWENPH1\ue001, направляем \ue000QWENPH0\ue001.",
        "Уважаемый \ue000QWENPH0\ue001, направляем \ue000QWENPH1\ue001. Номер 12345.",
        "Полностью другой большой текст \ue000QWENPH0\ue001 и \ue000QWENPH1\ue001 без исходного смысла.",
    ],
)
def test_unsafe_model_output_falls_back_to_original(bad_output):
    text = "Уважаемый [ФИО_1], направляем [Наименование_1]."
    processor = QwenPlaceholderPostprocessor(
        {"enabled": True}, model_loader=lambda settings: FakeModel(lambda prompt: bad_output)
    )
    assert processor.process(text) == text
    assert processor.last_status.startswith("rejected:")


def test_missing_model_is_safe_and_failure_is_memoized():
    attempts = []

    def unavailable(settings):
        attempts.append(settings)
        raise FileNotFoundError("cache miss")

    text = "Для [ФИО_1] подготовлен документ."
    processor = QwenPlaceholderPostprocessor({"enabled": True}, model_loader=unavailable)
    assert processor.process(text) == text
    assert processor.process(text) == text
    assert len(attempts) == 1


def test_device_selection_unknown_and_cpu_are_safe():
    assert choose_device("bogus", torch_module=None) == "cpu"
    assert choose_device("cpu", torch_module=None) == "cpu"


def test_cancelled_process_is_noop_without_loading():
    loaded = []
    processor = QwenPlaceholderPostprocessor(
        {"enabled": True}, model_loader=lambda settings: loaded.append(settings)
    )
    text = "Для [ФИО_1] подготовлен документ."
    assert processor.process(text, cancel_check=lambda: True) == text
    assert processor.last_status == "cancelled"
    assert loaded == []


def test_cancel_check_is_forwarded_and_cancels_after_generation():
    class CancellableModel:
        def __init__(self):
            self.cancel_check = None

        def generate(self, prompt, *, max_new_tokens, temperature, cancel_check=None):
            self.cancel_check = cancel_check
            return _document(prompt)

    model = CancellableModel()
    calls = [0]

    def cancelled():
        calls[0] += 1
        return calls[0] > 1

    processor = QwenPlaceholderPostprocessor(
        {"enabled": True}, model_loader=lambda settings: model
    )
    text = "Для [ФИО_1] подготовлен документ."
    assert processor.process(text, cancel_check=cancelled) == text
    assert model.cancel_check is cancelled
    assert processor.last_status == "cancelled"


def test_backend_forwards_operation_cancellation_to_qwen():
    from backend_api import BackendApi

    api = BackendApi.__new__(BackendApi)
    api.is_cancelled = lambda: True
    api._qwen_postprocessor = QwenPlaceholderPostprocessor(
        {"enabled": True}, model_loader=lambda _settings: pytest.fail("model loaded after cancellation")
    )
    text = "Для [ФИО_1] подготовлен документ."
    assert api.postprocess_anonymized_text(text) == text
    assert api._qwen_postprocessor.last_status == "cancelled"


def test_generation_cancel_exception_reports_cancelled_not_rejected():
    class StoppedModel:
        def generate(self, _prompt, **_kwargs):
            raise QwenGenerationCancelled("stop")

    processor = QwenPlaceholderPostprocessor(
        {"enabled": True}, model_loader=lambda _settings: StoppedModel()
    )
    text = "Для [ФИО_1] подготовлен документ."
    assert processor.process(text, cancel_check=lambda: False) == text
    assert processor.last_status == "cancelled"


def test_model_root_symlink_is_rejected(tmp_path, monkeypatch):
    real = tmp_path / "real-model"
    real.mkdir()
    link = tmp_path / "model-link"
    try:
        link.symlink_to(real, target_is_directory=True)
    except OSError:
        pytest.skip("Symlink creation requires administrative privileges on Windows")
    monkeypatch.setattr("qwen_offline.validate_model", lambda *args, **kwargs: True)
    settings = QwenPostprocessorSettings(enabled=True, model_path=str(link))
    with pytest.raises(ValueError, match="absolute local model directory"):
        _verified_local_model(settings)

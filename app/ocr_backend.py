# -*- coding: utf-8 -*-
"""Cross-platform OCR adapters used by :mod:`pdf_convert`.

The module deliberately imports OCR engines lazily.  Importing DOCXdodyr must
not download a model or require a command line OCR executable.  On macOS Apple
Vision is first, followed by a locally installed Tesseract and optional
PaddleOCR fallback. Other platforms retain their own adapter order.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from io import BytesIO
import importlib.util
import os
import re
import shutil
import sys
from pathlib import Path
from numbers import Real
from typing import Any, Callable, Iterable, Sequence
import app_paths


class OCRBackendUnavailable(RuntimeError):
    """Raised when no supported OCR engine is installed for this OS."""


class OCRBackendError(RuntimeError):
    """Raised when an installed OCR engine cannot recognize an image."""


class OCRCancelled(RuntimeError):
    """OCR was stopped by the caller before a complete result was produced."""


@dataclass(frozen=True)
class OCRBackendInfo:
    name: str
    label: str
    platform: str


@dataclass(frozen=True)
class OCRTextRegion:
    """One OCR observation in the application's canonical coordinate system.

    ``bbox`` is always ``(x1, y1, x2, y2)`` in normalized page coordinates,
    with the origin in the top-left corner and all values clamped to 0..1.
    Adapters may leave ``page`` at zero; the PDF orchestrator assigns the
    one-based page number after recognition.
    """

    text: str
    page: int = 0
    bbox: tuple[float, float, float, float] = (0.0, 0.0, 1.0, 1.0)
    confidence: float = 0.0

    def with_page(self, page: int) -> "OCRTextRegion":
        return replace(self, page=int(page))


# Предкомпилированные регулярные выражения для коррекции типичных ошибок OCR
_OCR_LEGAL_CORRECTIONS: tuple[tuple[re.Pattern[str], str], ...] = (
    # 2.1. Исправление триплетов нулей в числах и денежных суммах
    (re.compile(r"(\b\d+)\s+(?![0-9]{3}\b)[OО0]{3}\b(?!\s*[«\"“])"), r"\1 000"),
    (re.compile(r"(\b\d+)\s+(?![0-9]{3}\b)[OО0]{3}\b(?!\s*[«\"“])"), r"\1 000"),
    (re.compile(r"(\b\d+)\s*(?![0-9]{3}\b)[OО0]{3}(?=\s*[\.,]\d{2}\b|\s*(?:руб|коп|₽|USD|EUR|\([А-Яа-яЁё]))"), r"\1 000"),
    # 2.2. Компания ООО / АО / ЗАО / ПАО и юридические шаблоны
    (re.compile(r"(?<!\d)(?<!\d\s)\b(?:[OО0Uu]{3}|[OО0][OО0Uu][OО0Uu]|OOO|OOU|OUO|UOO|OUU|0ОО|О00|00О|0O0|O0O|000|00U|O0U)\s*([«\"“„])", re.I), r"ООО \1"),
    (re.compile(r"(?<!\d)(?<!\d\s)\b(?:AO|АО|A0|А0)\s*([«\"“„])", re.I), r"АО \1"),
    (re.compile(r"(?<!\d)(?<!\d\s)\b(?:3AO|3A0|ЗAO|ЗA0|ЗАО)\s*([«\"“„])", re.I), r"ЗАО \1"),
    (re.compile(r"(?<!\d)(?<!\d\s)\b(?:ПAO|ПA0|ПАО)\s*([«\"“„])", re.I), r"ПАО \1"),
    (re.compile(r"(?<!\d)(?<!\d\s)\b(?:OOO|OOU)\b(?!\s*[\d\(\.,])", re.I), "ООО"),
    (re.compile(r"\b(?:11риложение|IpnJI0KeHM1|Ipnложение)\b"), "Приложение"),
    (re.compile(r"\b0тделени[ея]\b", re.I), "отделение"),
    (re.compile(r"\b(?:0бществ[оа]|O6uecTBO|O6mecTBo|OFIHIECTBO)\b", re.I), "Общество"),
    (re.compile(r"\b(?:OTPH|OГPH)\b"), "ОГРН"),
    (re.compile(r"\b(?:HHH|HНH|WHH|ИHН|IНН)\b"), "ИНН"),
    (re.compile(r"\b(?:KIIII|KПП)\b"), "КПП"),
    (re.compile(r"\b(?:Cч|Cu|C4)\s*\.\s*№", re.I), "Сч. №"),
    (re.compile(r"\b(?:TY|ГУ)\s+Банка\s+России\b", re.I), "ГУ Банка России"),
    (re.compile(r"\b(?:NO|NО)\s+ЦФО\b"), "ПО ЦФО"),
    (re.compile(r"\bБИ[кК]\b"), "БИК"),
    (re.compile(r"\bФорма\s+no\s+ОКУД\b", re.I), "Форма по ОКУД"),
    (re.compile(r"\bбанховские\s+рехвизиты\b", re.I), "банковские реквизиты"),
    (re.compile(r"\b(?:IpaKIaHHH|TpaxIaHNH|ГpaKIaHHH)\b"), "Гражданин"),
    (re.compile(r"\b(?:OTBeTYHK|OTBeTyHK)\b"), "Ответчик"),
    (re.compile(r"\b(?:HpeICTaBHTeIb|IIpeICTaBHTeIb)\b"), "Представитель"),
    (re.compile(r"\b(?:3aKa3uHK|3aKa3чик)\b"), "Заказчик"),
    (re.compile(r"\b(?:HcnonHHTeJb|Hcnолнитель)\b"), "Исполнитель"),
    (re.compile(r"\b(?:BHIIMCKA|BbIUCKH|BbINCKA)\b"), "ВЫПИСКА"),
    (re.compile(r"\b(?:JOTOBOP|IOTOВОР)\b"), "ДОГОВОР"),
    (re.compile(r"\b(?:PE3OJIIOTHBHAA|PE3OЛЮТИВНАЯ)\b"), "РЕЗОЛЮТИВНАЯ"),
    (re.compile(r"\b(?:HACTb|HAСТЬ)\b"), "ЧАСТЬ"),
    (re.compile(r"\b(?:PEILEHHA|PEWEHHA)\b"), "РЕШЕНИЯ"),
    (re.compile(r"\b(?:APEMTPAKHBIM|APEUIPAKHBIM)\b"), "АРБИТРАЖНЫЙ"),
    (re.compile(r"\bCYITOPOIA\b"), "СУД ГОРОДА"),
    (re.compile(r"\b(?:cyI|CyI)\b"), "суд"),
    (re.compile(r"\b(?:DeIepauHn|ΦeIepaINN|ФeIepauNN)\b"), "Федерации"),
    (re.compile(r"\b(?:JeJIo|IeJIo)\b"), "Дело"),
)


def normalize_ocr_line(text: str) -> str:
    """Clean common OCR artifacts in a single recognized line."""
    if not text:
        return ""

    s = str(text)
    # 1. Standard punctuation and quotes
    # Single angle brackets are meaningful in contracts, code and formulas.
    # Only dedicated Unicode quote variants are safe to normalize.
    s = s.replace("《", "«").replace("》", "»")
    s = re.sub(r"\bN[º°o]\.?\s*", "№ ", s)
    # Normalize double/triple @ signs in email: @@ -> @
    s = re.sub(r"\s*@{2,}\s*", "@", s)
    # Fix accidental space inside email local-part before @: e.g. "user 01@example.invalid" -> "user01@example.invalid"
    s = re.sub(r"\b([A-Za-z0-9._%+-]+)\s+([A-Za-z0-9._%+-]+)\s*@\s*([A-Za-z0-9.-]+)\s*\.\s*([A-Za-z]{2,})", r"\1\2@\3.\4", s)
    s = re.sub(r"([A-Za-z0-9._%+-]+)\s*@\s*([A-Za-z0-9.-]+)\s*\.\s*([A-Za-z]{2,})", r"\1@\2.\3", s)

    # 2. Russian legal boilerplate and entities OCR fixes
    for pattern, replacement in _OCR_LEGAL_CORRECTIONS:
        s = pattern.sub(replacement, s)

    # 3. Cyrillic digram & character substitutions
    s = re.sub(r"(?<=[А-Яа-яA-Za-z])bI(?=[А-Яа-яA-Za-z]|\b)", "ы", s)
    s = re.sub(r"(?<=[А-Яа-яA-Za-z])BI(?=[А-Яа-яA-Za-z]|\b)", "Ы", s)
    s = s.replace("中", "Ф").replace("Φ", "Ф")

    # 4. Spacing cleanup
    s = re.sub(r"«\s+(\d+)\s+»", r"«\1»", s)
    s = re.sub(r"«\s+", "«", s)
    s = re.sub(r"\s+»", "»", s)
    s = re.sub(r"[\s\t]+", " ", s)
    return s.strip()


def sort_and_group_page_regions(regions: Sequence[OCRTextRegion], line_height_tol: float = 0.015) -> list[str]:
    """Sort OCR regions in natural 2D reading order (top-to-bottom, left-to-right)."""
    if not regions:
        return []

    valid_regions = [r for r in regions if r.text and r.text.strip()]
    if not valid_regions:
        return []

    # If regions have no spatial coordinates (mock / legacy adapters), preserve lines as-is
    if all(r.bbox == (0.0, 0.0, 1.0, 1.0) for r in valid_regions):
        return [normalize_ocr_line(r.text) for r in valid_regions if r.text.strip()]

    def y_mid(r: OCRTextRegion) -> float:
        return (r.bbox[1] + r.bbox[3]) / 2.0


    sorted_by_y = sorted(valid_regions, key=lambda r: (r.bbox[1], r.bbox[0]))

    lines: list[list[OCRTextRegion]] = []
    current_line = [sorted_by_y[0]]

    for r in sorted_by_y[1:]:
        curr_y_mid = sum(y_mid(item) for item in current_line) / len(current_line)
        curr_h = max(item.bbox[3] - item.bbox[1] for item in current_line)
        r_y = y_mid(r)

        tol = max(line_height_tol, curr_h * 0.45)
        if abs(r_y - curr_y_mid) <= tol:
            current_line.append(r)
        else:
            current_line.sort(key=lambda item: item.bbox[0])
            lines.append(current_line)
            current_line = [r]

    if current_line:
        current_line.sort(key=lambda item: item.bbox[0])
        lines.append(current_line)

    normalized_lines: list[str] = []
    for line in lines:
        line_text = " ".join(normalize_ocr_line(r.text) for r in line if r.text.strip())
        if line_text:
            normalized_lines.append(line_text)

    return normalized_lines


@dataclass(frozen=True)
class OCRPageResult:
    """OCR results for one page, including the rendered pixel dimensions."""

    page: int
    width: int
    height: int
    regions: tuple[OCRTextRegion, ...] = ()
    native_text: str = ""
    source: str = "ocr"

    @property
    def text(self) -> str:
        if self.source == "text":
            return self.native_text
        lines = sort_and_group_page_regions(self.regions)
        if lines:
            return "\n".join(lines)
        return "\n".join(region.text for region in self.regions if region.text)



@dataclass(frozen=True)
class OCRDocumentResult:
    """Stable OCR contract shared by Apple Vision and ONNX/Paddle adapters."""

    text: str
    pages: tuple[OCRPageResult, ...]
    backend: str = ""

    @property
    def regions(self) -> tuple[OCRTextRegion, ...]:
        return tuple(region for page in self.pages for region in page.regions)


def _normalise_confidence(value: Any) -> float:
    """Convert engine confidence values (including percentages) to 0..1."""

    try:
        number = float(value)
    except (TypeError, ValueError):
        return 0.0
    if number != number:  # NaN
        return 0.0
    if number > 1.0 and number <= 100.0:
        number /= 100.0
    return max(0.0, min(1.0, number))


def normalize_confidence(value: Any) -> float:
    """Public spelling of the canonical 0..1 confidence normalization."""

    return _normalise_confidence(value)


def _points_to_xyxy(box: Any) -> tuple[float, float, float, float] | None:
    """Accept xyxy, xywh, or a quadrilateral and return raw xyxy values."""

    if box is None:
        return None
    try:
        values = list(box)
    except TypeError:
        return None
    if len(values) == 4 and all(isinstance(item, Real) for item in values):
        x1, y1, x2, y2 = (float(item) for item in values)
        return (min(x1, x2), min(y1, y2), max(x1, x2), max(y1, y2))
    points: list[tuple[float, float]] = []
    for point in values:
        try:
            coords = list(point)
            if len(coords) >= 2:
                points.append((float(coords[0]), float(coords[1])))
        except (TypeError, ValueError):
            continue
    if not points:
        return None
    xs, ys = zip(*points)
    return (min(xs), min(ys), max(xs), max(ys))


def normalize_bbox(
    box: Any,
    width: int | float,
    height: int | float,
    *,
    origin: str = "top-left",
    already_normalized: bool = False,
) -> tuple[float, float, float, float]:
    """Normalize an engine bbox to canonical top-left normalized xyxy."""

    raw = _points_to_xyxy(box)
    if raw is None:
        return (0.0, 0.0, 1.0, 1.0)
    if already_normalized:
        x1, y1, x2, y2 = raw
    else:
        w = max(float(width or 1), 1.0)
        h = max(float(height or 1), 1.0)
        x1, y1, x2, y2 = (raw[0] / w, raw[1] / h, raw[2] / w, raw[3] / h)
    if origin.lower() in {"bottom-left", "bottom_left", "vision"}:
        y1, y2 = 1.0 - y2, 1.0 - y1
    x1, x2 = sorted((x1, x2))
    y1, y2 = sorted((y1, y2))
    return tuple(max(0.0, min(1.0, value)) for value in (x1, y1, x2, y2))


def _region(text: Any, box: Any, confidence: Any, width: int, height: int, *, origin: str = "top-left", normalized: bool = False) -> OCRTextRegion | None:
    value = str(text or "").strip()
    if not value:
        return None
    return OCRTextRegion(value, bbox=normalize_bbox(box, width, height, origin=origin, already_normalized=normalized), confidence=_normalise_confidence(confidence))


def _module_available(name: str) -> bool:
    try:
        return importlib.util.find_spec(name) is not None
    except (ImportError, ModuleNotFoundError, ValueError):
        return False


def _find_tessdata_dir() -> str | None:
    """Find directory containing traineddata files (rus.traineddata / eng.traineddata)."""
    env_dir = os.environ.get("TESSDATA_PREFIX")
    if env_dir:
        p = Path(env_dir)
        if (p / "rus.traineddata").is_file():
            return str(p)
        if (p / "tessdata" / "rus.traineddata").is_file():
            return str(p / "tessdata")
    candidates = [
        Path(getattr(sys, "_MEIPASS", "")) / "resources" / "tessdata" if getattr(sys, "_MEIPASS", None) else None,
        Path(sys.executable).parent / "resources" / "tessdata",
        Path(sys.executable).parent / "_internal" / "resources" / "tessdata",
        app_paths.get_bundle_dir() / "resources" / "tessdata",
        app_paths.get_bundle_dir() / "tessdata",
        Path.home() / ".docxdodyr" / "tessdata",
        Path(os.environ.get("LOCALAPPDATA", "")) / "Programs" / "Tesseract-OCR" / "tessdata" if os.environ.get("LOCALAPPDATA") else None,
        Path(r"C:\Program Files\Tesseract-OCR\tessdata"),
    ]
    for candidate in candidates:
        if candidate and (candidate / "rus.traineddata").is_file():
            return str(candidate)
    return None


def _tesseract_command() -> str | None:
    """Find a local Tesseract binary, including Homebrew paths absent from Finder's PATH."""
    command = shutil.which("tesseract")
    if command:
        return command
    if sys.platform == "darwin":
        for candidate in ("/opt/homebrew/bin/tesseract", "/usr/local/bin/tesseract"):
            if Path(candidate).is_file() and os.access(candidate, os.X_OK):
                return candidate
    if sys.platform == "win32":
        win_candidates = [
            os.environ.get("TESSERACT_PATH"),
            r"C:\Program Files\Tesseract-OCR\tesseract.exe",
            r"C:\Program Files (x86)\Tesseract-OCR\tesseract.exe",
            str(Path(os.environ.get("LOCALAPPDATA", "")) / "Programs" / "Tesseract-OCR" / "tesseract.exe") if os.environ.get("LOCALAPPDATA") else None,
            str(app_paths.get_bundle_dir() / "resources" / "tesseract_win" / "tesseract.exe"),
        ]
        for candidate in win_candidates:
            if candidate and Path(candidate).is_file():
                return candidate
    return None


def available_backend_names(platform_name: str | None = None) -> tuple[str, ...]:
    """Return lightweight availability information without loading models."""

    platform_name = platform_name or sys.platform
    result: list[str] = []
    if platform_name == "darwin" and _module_available("Vision") and _module_available("Quartz"):
        result.append("applevision")
    if _module_available("paddleocr"):
        result.append("paddleocr")
    if (_module_available("pytesseract") and _tesseract_command()) or (_module_available("fitz") and _find_tessdata_dir()):
        result.append("tesseract")
    return tuple(result)


def pdf_ocr_dependencies_available(platform_name: str | None = None) -> bool:
    """Whether PDF rendering and at least one OCR adapter are importable."""

    return _module_available("fitz") and bool(available_backend_names(platform_name))


def _language_list(language: str | None) -> list[str]:
    values = [part.strip() for part in (language or "rus+eng").replace(",", "+").split("+")]
    return [value for value in values if value]


class _AppleVisionBackend:
    name = "applevision"
    label = "Apple Vision"

    def __init__(self, language: str = "rus+eng") -> None:
        try:
            import Quartz  # type: ignore
            import Vision  # type: ignore
            from Cocoa import NSURL  # type: ignore
            from Foundation import NSDictionary, NSData  # type: ignore
        except Exception as exc:  # pragma: no cover - platform dependent
            raise OCRBackendUnavailable(
                "Apple Vision недоступен: требуются системные фреймворки macOS "
                "и pyobjc-framework-Vision."
            ) from exc
        self._Quartz = Quartz
        self._Vision = Vision
        self._NSURL = NSURL
        self._NSDictionary = NSDictionary
        self._NSData = NSData
        self._languages = _language_list(language)

    def recognize_regions(self, image_bytes: bytes) -> list[OCRTextRegion]:  # pragma: no cover - platform dependent
        # Feed encoded PNG bytes directly to Vision.  The former CIImage/URL
        # bridge can return ``success=False, error=None`` in sandboxed macOS
        # processes even for a valid image.
        image_data = self._NSData.dataWithBytes_length_(image_bytes, len(image_bytes))
        handler = self._Vision.VNImageRequestHandler.alloc().initWithData_options_(
            image_data, self._NSDictionary.dictionaryWithDictionary_({})
        )
        if handler is None:
            raise OCRBackendError("Apple Vision не смог открыть изображение страницы.")
        try:
            results: list[OCRTextRegion] = []

            def completion(request: Any, error: Any) -> None:
                if error:
                    return
                for observation in request.results() or []:
                    candidates = observation.topCandidates_(1)
                    if candidates:
                        candidate = candidates[0]
                        box = observation.boundingBox()
                        try:
                            if hasattr(box, "origin") and hasattr(box, "size"):
                                x1 = float(box.origin.x)
                                y1 = 1.0 - float(box.origin.y + box.size.height)
                                x2 = float(box.origin.x + box.size.width)
                                y2 = 1.0 - float(box.origin.y)
                            elif len(box) == 2 and hasattr(box[0], "__iter__"):
                                x1 = float(box[0][0])
                                y1 = 1.0 - float(box[0][1] + box[1][1])
                                x2 = float(box[0][0] + box[1][0])
                                y2 = 1.0 - float(box[0][1])
                            else:
                                vx, vy, vw, vh = (float(v) for v in box)
                                x1, y1, x2, y2 = vx, 1.0 - (vy + vh), vx + vw, 1.0 - vy
                            vision_box = (x1, y1, x2, y2)
                        except Exception:
                            vision_box = (0.0, 0.0, 1.0, 1.0)
                        region = _region(
                            candidate.string(),
                            vision_box,
                            candidate.confidence(),
                            1,
                            1,
                            origin="top-left",
                            normalized=True,
                        )
                        if region:
                            results.append(region)

            request = self._Vision.VNRecognizeTextRequest.alloc().initWithCompletionHandler_(completion)
            request.setRecognitionLevel_(self._Vision.VNRequestTextRecognitionLevelAccurate)
            try:
                request.setUsesLanguageCorrection_(True)
            except Exception:
                pass
            # Vision expects BCP-47 language tags rather than short OCR codes.
            mapping = {"rus": "ru-RU", "eng": "en-US"}
            languages = [mapping.get(value, value) for value in self._languages]
            if languages:
                try:
                    request.setRecognitionLanguages_(languages)
                except Exception:
                    pass
            success, error = handler.performRequests_error_([request], None)
            if not success:
                raise OCRBackendError(f"Apple Vision не выполнил запрос: {error}")
            return results
        except OCRBackendError:
            raise
        except Exception as exc:
            raise OCRBackendError(f"Apple Vision завершился с ошибкой: {exc}") from exc

    def recognize(self, image_bytes: bytes) -> list[str]:  # pragma: no cover - compatibility
        return [region.text for region in self.recognize_regions(image_bytes)]


class _PaddleOCRBackend:
    name = "paddleocr"
    label = "PaddleOCR"

    def __init__(self, language: str = "rus+eng") -> None:
        try:
            from paddleocr import PaddleOCR  # type: ignore

            # Explicitly avoid network/model management flags here.  Paddle's
            # normal local cache is used; an absent model yields a clear error.
            # PaddleOCR accepts short language names (``ru``/``en``), while
            # the public OCR API also uses Tesseract-style ``rus``/``eng``.
            languages = _language_list(language)
            if any(value in {"ru", "rus"} for value in languages):
                paddle_language = "ru"
            elif any(value in {"en", "eng"} for value in languages):
                paddle_language = "en"
            else:
                paddle_language = "ru"
            self._ocr = PaddleOCR(lang=paddle_language)
        except Exception as exc:
            raise OCRBackendUnavailable(
                "PaddleOCR недоступен. Установите paddleocr и подготовьте локальные модели."
            ) from exc

    def recognize_regions(self, image_bytes: bytes) -> list[OCRTextRegion]:
        try:
            import numpy as np  # type: ignore
            from PIL import Image  # type: ignore

            image_obj = Image.open(BytesIO(image_bytes)).convert("RGB")
            width, height = image_obj.size
            image = np.asarray(image_obj)
            result = self._ocr.ocr(image)
            return _paddle_regions(result, width, height)
        except Exception as exc:
            raise OCRBackendError(f"PaddleOCR не смог распознать страницу: {exc}") from exc

    def recognize(self, image_bytes: bytes) -> list[str]:
        return [region.text for region in self.recognize_regions(image_bytes)]


class _TesseractOCRBackend:
    name = "tesseract"
    label = "Tesseract (LSTM Neural)"

    def __init__(self, language: str = "rus+eng") -> None:
        mapping = {"ru": "rus", "en": "eng", "rus+eng": "rus+eng"}
        self._lang = mapping.get(language, language)
        self._engine = None
        self._tessdata_dir = _find_tessdata_dir()
        if self._tessdata_dir and not os.environ.get("TESSDATA_PREFIX"):
            os.environ["TESSDATA_PREFIX"] = self._tessdata_dir

        command = _tesseract_command()
        if command and _module_available("pytesseract"):
            try:
                import pytesseract
                pytesseract.pytesseract.tesseract_cmd = command
                pytesseract.get_tesseract_version()
                self._pytesseract = pytesseract
                self._engine = "pytesseract"
            except Exception:
                self._engine = None

        if not self._engine:
            if _module_available("fitz") and self._tessdata_dir:
                self._engine = "pymupdf"
            else:
                raise OCRBackendUnavailable(
                    "Tesseract OCR недоступен: установите tesseract и pytesseract либо предоставьте языковые данные tessdata."
                )

    def recognize_regions(self, image_bytes: bytes) -> list[OCRTextRegion]:
        if self._engine == "pymupdf":
            try:
                import fitz
                img_doc = fitz.open(stream=image_bytes, filetype="png")
                pdf_bytes = img_doc.convert_to_pdf()
                pdf_doc = fitz.open("pdf", pdf_bytes)
                page = pdf_doc[0]
                pw, ph = page.rect.width, page.rect.height
                tp = page.get_textpage_ocr(language=self._lang, tessdata=self._tessdata_dir)
                d = page.get_text("dict", textpage=tp)
                results: list[OCRTextRegion] = []
                for b in d.get("blocks", []):
                    for l in b.get("lines", []):
                        line_text = "".join(s.get("text", "") for s in l.get("spans", [])).strip()
                        if not line_text:
                            continue
                        lx0, ly0, lx1, ly1 = l.get("bbox")
                        reg = _region(line_text, (lx0, ly0, lx1, ly1), 0.85, pw, ph)
                        if reg:
                            results.append(reg)
                return results
            except Exception as exc:
                raise OCRBackendError(f"PyMuPDF Tesseract не смог распознать страницу: {exc}") from exc

        try:
            from PIL import Image
            image_obj = Image.open(BytesIO(image_bytes)).convert("RGB")
            # Tesseract does not reliably auto-rotate scanned forms. OSD is
            # cheap compared with recognition and prevents an entire page
            # from being returned in reverse/column-scrambled order.
            try:
                osd = self._pytesseract.image_to_osd(
                    image_obj, output_type=self._pytesseract.Output.STRING
                )
                rotate_match = re.search(r"^Rotate:\s*(\d+)", osd, re.M)
                confidence_match = re.search(
                    r"^Orientation confidence:\s*([0-9.]+)", osd, re.M
                )
                rotation = int(rotate_match.group(1)) if rotate_match else 0
                confidence = float(confidence_match.group(1)) if confidence_match else 0.0
                if rotation in {90, 180, 270} and confidence >= 2.0:
                    image_obj = image_obj.rotate(-rotation, expand=True)
            except Exception:
                # Sparse pages often do not have enough characters for OSD;
                # normal OCR remains the safe fallback.
                pass
            width, height = image_obj.size
            data = self._pytesseract.image_to_data(
                image_obj, lang=self._lang, output_type=self._pytesseract.Output.DICT
            )
            lines_map: dict[tuple[int, int, int], list[dict]] = {}
            n_boxes = len(data.get("text", []))
            for i in range(n_boxes):
                text = str(data["text"][i] or "").strip()
                if not text:
                    continue
                key = (int(data["block_num"][i]), int(data["par_num"][i]), int(data["line_num"][i]))
                if key not in lines_map:
                    lines_map[key] = []
                lines_map[key].append({
                    "text": text,
                    "left": int(data["left"][i]),
                    "top": int(data["top"][i]),
                    "width": int(data["width"][i]),
                    "height": int(data["height"][i]),
                    "conf": float(data["conf"][i]),
                })
            results: list[OCRTextRegion] = []
            for words in lines_map.values():
                if not words:
                    continue
                line_text = " ".join(w["text"] for w in words).strip()
                if not line_text:
                    continue
                min_x = min(w["left"] for w in words)
                min_y = min(w["top"] for w in words)
                max_x = max(w["left"] + w["width"] for w in words)
                max_y = max(w["top"] + w["height"] for w in words)
                confs = [w["conf"] for w in words if w["conf"] >= 0]
                avg_conf = sum(confs) / len(confs) if confs else 0.0
                reg = _region(line_text, (min_x, min_y, max_x, max_y), avg_conf, width, height)
                if reg:
                    results.append(reg)
            return results
        except Exception as exc:
            raise OCRBackendError(f"Tesseract не смог распознать страницу: {exc}") from exc

    def recognize(self, image_bytes: bytes) -> list[str]:
        return [region.text for region in self.recognize_regions(image_bytes)]


def _paddle_regions(output: Any, width: int, height: int) -> list[OCRTextRegion]:
    """Normalize PaddleOCR v2/v3 dictionaries and classic nested results."""

    if isinstance(output, (list, tuple)) and output and all(isinstance(item, dict) for item in output):
        result = []
        for item in output:
            result.extend(_paddle_regions(item, width, height))
        if result:
            return result
    if isinstance(output, dict):
        boxes = output.get("dt_polys") or output.get("rec_polys") or output.get("boxes") or []
        texts = output.get("rec_texts") or output.get("texts") or output.get("text") or []
        scores = output.get("rec_scores") or output.get("scores") or []
        if isinstance(texts, str):
            texts = [texts]
        result = []
        for index, text in enumerate(texts):
            box = boxes[index] if index < len(boxes) else None
            score = scores[index] if index < len(scores) else 0.0
            region = _region(text, box, score, width, height)
            if region:
                result.append(region)
        if result:
            return result
    result = []
    queue = list(output) if isinstance(output, (list, tuple)) else [output]
    while queue:
        item = queue.pop(0)
        if isinstance(item, dict):
            queue.extend(item.values())
            continue
        if isinstance(item, (list, tuple)):
            if len(item) >= 2:
                box, text_score = item[0], item[1]
                if isinstance(text_score, (list, tuple)):
                    text = text_score[0] if text_score else ""
                    score = text_score[1] if len(text_score) > 1 else 0.0
                    region = _region(text, box, score, width, height)
                    if region:
                        result.append(region)
                        continue
            queue.extend(item)
    return result


def create_ocr_backend(
    preferred: str | None = None,
    language: str = "rus+eng",
    platform_name: str | None = None,
    excluded: Iterable[str] = (),
) -> tuple[OCRBackendInfo, Any]:
    """Create the first usable backend for the current OS.

    ``preferred`` is treated as a hint.  A failed optional engine is skipped
    and the next local backend is tried; no model download or network call is
    performed by this selector.
    """

    platform_name = platform_name or sys.platform
    excluded = set(excluded or ())
    factories: dict[str, Callable[[], Any]] = {
        "applevision": lambda: _AppleVisionBackend(language),
        "paddleocr": lambda: _PaddleOCRBackend(language),
        "tesseract": lambda: _TesseractOCRBackend(language),
    }
    if platform_name == "darwin":
        defaults = ["applevision", "tesseract", "paddleocr"]
    else:
        defaults = ["tesseract", "paddleocr"]
    candidates = []
    # Apple Vision is a macOS system framework; never honor that preference
    # on Windows/Linux even if a similarly named Python package is installed.
    if (
        preferred in factories
        and preferred not in excluded
        and not (preferred == "applevision" and platform_name != "darwin")
    ):
        candidates.append(preferred)
    candidates.extend(name for name in defaults if name not in candidates and name not in excluded)
    failures: list[str] = []
    for name in candidates:
        try:
            backend = factories[name]()
            return OCRBackendInfo(name, backend.label, platform_name), backend
        except OCRBackendUnavailable as exc:
            failures.append(f"{name}: {exc}")
    supported = ", ".join(available_backend_names(platform_name)) or "нет"
    details = "; ".join(failures) if failures else "поддерживаемые библиотеки не установлены"
    raise OCRBackendUnavailable(
        f"OCR для {platform_name} недоступен. Доступные адаптеры: {supported}. {details}"
    )


def ocr_image_to_result(
    image_path: Any,
    *,
    ocr_lang: str = "rus+eng",
    preferred_backend: str | None = None,
    cancel_check: Callable[[], bool] | None = None,
) -> OCRDocumentResult:
    """Распознает изображение (JPG, PNG, TIFF, BMP, WEBP) через доступный OCR движок."""
    from pathlib import Path
    path_obj = Path(image_path)
    if cancel_check and cancel_check():
        raise OCRCancelled("Распознавание изображения отменено пользователем")
    if not path_obj.exists():
        raise FileNotFoundError(f"Файл изображения не найден: {image_path}")

    image_bytes = path_obj.read_bytes()
    excluded = set()
    last_err = None
    backend_info = None
    raw_regions = []

    while True:
        try:
            backend_info, backend = create_ocr_backend(
                preferred=preferred_backend,
                language="rus+eng" if ocr_lang in ("rus+eng", "all") else ocr_lang,
                excluded=excluded,
            )
        except OCRBackendUnavailable:
            if last_err:
                raise last_err
            raise

        recognize_regions = getattr(backend, "recognize_regions", None)
        try:
            if callable(recognize_regions):
                raw_regions = recognize_regions(image_bytes)
            else:
                lines = backend.recognize(image_bytes)
                raw_regions = [OCRTextRegion(str(line).strip()) for line in lines if str(line).strip()]
            if cancel_check and cancel_check():
                raise OCRCancelled("Распознавание изображения отменено пользователем")
            if not any(normalize_ocr_line(region.text).strip() for region in raw_regions):
                last_err = OCRBackendError("OCR не извлёк читаемый текст из изображения")
                excluded.add(backend_info.name)
                continue
            break
        except OCRCancelled:
            raise
        except Exception as exc:
            last_err = exc
            excluded.add(backend_info.name)
            continue

    normalized_regions = []
    for r in raw_regions:
        txt = normalize_ocr_line(r.text).strip()
        if txt:
            normalized_regions.append(
                OCRTextRegion(
                    text=txt,
                    page=1,
                    bbox=r.bbox,
                    confidence=r.confidence,
                )
            )

    full_text = "\n".join(r.text for r in normalized_regions)
    # Keep the result geometry truthful for image callers.  OCR backends use
    # normalized boxes, but consumers also rely on page dimensions when
    # rendering highlights or exporting review artifacts.
    image_width = image_height = 1000
    try:
        from PIL import Image

        with Image.open(BytesIO(image_bytes)) as image:
            image_width, image_height = (int(image.width), int(image.height))
    except Exception:
        # Apple Vision can decode formats without Pillow installed.  Preserve
        # the historical fallback dimensions in that unusual environment.
        pass
    page_res = OCRPageResult(
        page=1,
        width=image_width,
        height=image_height,
        regions=tuple(normalized_regions),
    )
    return OCRDocumentResult(
        text=full_text,
        pages=(page_res,),
        backend=backend_info.name,
    )


__all__ = [
    "OCRBackendError",
    "OCRCancelled",
    "OCRBackendInfo",
    "OCRBackendUnavailable",
    "OCRDocumentResult",
    "OCRPageResult",
    "OCRTextRegion",
    "available_backend_names",
    "create_ocr_backend",
    "normalize_bbox",
    "normalize_confidence",
    "ocr_image_to_result",
    "pdf_ocr_dependencies_available",
]

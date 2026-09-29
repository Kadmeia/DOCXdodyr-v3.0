# -*- coding: utf-8 -*-
import pytest
from pathlib import Path
from docx import Document
from validation import is_valid_iban, is_valid_swift_bic, validate_iban, validate_swift_bic, is_sufficient_address
from markdown_export import docx_to_markdown, text_to_markdown, save_as_markdown
from backend_api import BackendApi


def test_iban_validation():
    # Valid German IBAN (MOD 97-10)
    assert is_valid_iban("DE89370400440532013000") is True
    assert validate_iban("DE89370400440532013000") is not None
    # Valid British IBAN
    assert is_valid_iban("GB29NWBK60161331926819") is True
    # Invalid IBAN checksum
    assert is_valid_iban("DE89370400440532013001") is False
    # Short / invalid format
    assert is_valid_iban("DE123") is False


def test_swift_bic_validation():
    # Valid 8 and 11 char SWIFT BICs
    assert is_valid_swift_bic("DEUTDEDD") is True
    assert is_valid_swift_bic("DEUTDEDDXXX") is True
    assert validate_swift_bic("DEUTDEDD") is not None
    # Invalid SWIFT
    assert is_valid_swift_bic("DEUTD") is False
    assert is_valid_swift_bic("DEUTDEDD12345") is False


def test_is_sufficient_address_rules():
    # Неполные географические обозначения - НЕ адрес
    assert is_sufficient_address("Мальдивы, Адду Атолл") is False
    assert is_sufficient_address("Адду Атолл") is False
    assert is_sufficient_address("Мальдивы") is False
    assert is_sufficient_address("г. Москва") is False
    assert is_sufficient_address("г. Санкт-Петербург") is False
    assert is_sufficient_address("Россия, г. Москва") is False
    assert is_sufficient_address("Республика Татарстан, г. Набережные Челны") is False
    assert is_sufficient_address("115432, г. Москва") is False
    assert is_sufficient_address("Франция, Париж") is False

    # Полные адреса с улицей/районом и домом - АДРЕС
    assert is_sufficient_address("г. Москва, ул. Тверская, д. 1") is True
    assert is_sufficient_address("423800, Республика Татарстан, г. Набережные Челны, б-р Примерный, д. 1, кв. 10") is True
    assert is_sufficient_address("Россия, 101000, г. Москва, ул. Примерная, дом 1, корпус 1, этаж 1, оф. 10") is True
    assert is_sufficient_address("190000, РФ, г. Санкт-Петербург, Невский проспект, дом 1 Литера А, пом. 1") is True
    assert is_sufficient_address("Московская обл., Одинцовский р-н, пос. Барвиха, д. 10") is True
    assert is_sufficient_address("пос. Развилка, д. 12") is True
    assert is_sufficient_address("с. Усово, ул. Центральная, д. 1") is True
    assert is_sufficient_address("Ленинский р-н, 5") is True
    assert is_sufficient_address("Baker Street, 221B") is True
    assert is_sufficient_address("10 Downing Street, London") is True


def test_foreign_entities_and_atoll_masking():
    api = BackendApi()
    if not api.init_pullenti():
        pytest.skip("Pullenti not available")

    # Текст с иностранными организациями, именами, IBAN, SWIFT,
    # неполным гео ("Мальдивы, Адду Атолл" - сохраняется!)
    # и полным адресом с улицей и домом (маскируется!)
    text = (
        "Авиаперевозчик Air Arabia подтвердил бронирование. "
        "Отель Canareef Resort Maldives находится в Адду Атолл, Мальдивы. "
        "Пассажир Petrov Artem произвел оплату. "
        "Реквизиты: IBAN DE89370400440532013000, SWIFT DEUTDEDDXXX. "
        "Адрес офиса: г. Москва, ул. Тверская, д. 10, кв. 5."
    )

    mapping = {}
    seen = {}
    cleaned, count, logs = api.anonymize_text_pullenti(text, mapping_dict=mapping, entity_seen=seen)

    assert "Air Arabia" not in cleaned
    assert "[Name" in cleaned or "[Организация" in cleaned
    assert "Canareef Resort Maldives" not in cleaned
    # "Мальдивы, Адду Атолл" не являются полным адресом и НЕ должны относиться к [Адрес]
    assert "Адду Атолл" in cleaned
    assert "Мальдивы" in cleaned
    assert "DE89370400440532013000" not in cleaned
    assert "[IBAN" in cleaned
    assert "[SWIFT" in cleaned
    # Полноценный адрес с улицей и домом должен быть обезличен
    assert "ул. Тверская, д. 10, кв. 5" not in cleaned
    assert "[Адрес" in cleaned

def test_markdown_export(tmp_path):
    doc = Document()
    doc.add_heading("Ответ на претензию", level=1)
    doc.add_paragraph("Уважаемый [ФИО_1], сообщаем следующее:")
    table = doc.add_table(rows=2, cols=2)
    table.rows[0].cells[0].text = "Услуга"
    table.rows[0].cells[1].text = "Статус"
    table.rows[1].cells[0].text = "Перелет"
    table.rows[1].cells[1].text = "Отменен"

    md_path = tmp_path / "test_export.md"
    save_as_markdown(doc, md_path)
    assert md_path.exists()
    content = md_path.read_text(encoding="utf-8")
    assert "# Ответ на претензию" in content
    assert "| Услуга | Статус |" in content
    assert "| Перелет | Отменен |" in content

def test_independent_format_saving(tmp_path, monkeypatch):
    import backend_api
    from pdf_convert import convert_docx_text_to_pdf
    monkeypatch.setattr(backend_api, "convert_to_pdf", convert_docx_text_to_pdf)

    api = BackendApi()
    if not api.init_pullenti():
        pytest.skip("Pullenti not available")

    # Создаем тестовый docx
    src = tmp_path / "claim.docx"
    doc = Document()
    doc.add_paragraph("Гражданин Иванов Иван Иванович заключил договор № 12345.")
    doc.save(str(src))

    # 1. Сохранение только в Markdown
    api.save_original = False
    api.save_docx = False
    api.save_pdf = False
    api.save_markdown = True
    out_dir_md = tmp_path / "out_md"
    out_dir_md.mkdir()

    api.process_single_file(str(src), set(), set(), output_dir=out_dir_md)
    created_files_md = [f.name for f in out_dir_md.iterdir() if f.is_file() and not f.name.startswith(".")]
    assert any(f.endswith(".md") for f in created_files_md)
    assert not any(f.endswith(".docx") for f in created_files_md)
    assert not any(f.endswith(".pdf") for f in created_files_md)

    # 2. Сохранение во всех трех форматах: docx, pdf, md
    api.save_original = True
    api.save_docx = True
    api.save_pdf = True
    api.save_markdown = True
    out_dir_all = tmp_path / "out_all"
    out_dir_all.mkdir()

    api.process_single_file(str(src), set(), set(), output_dir=out_dir_all)
    created_files_all = [f.name for f in out_dir_all.iterdir() if f.is_file() and not f.name.startswith(".")]
    assert any(f.endswith(".docx") for f in created_files_all)
    assert any(f.endswith(".pdf") for f in created_files_all)
    assert any(f.endswith(".md") for f in created_files_all)


def test_image_ocr_processing(tmp_path, monkeypatch):
    import backend_api
    from pdf_convert import convert_docx_text_to_pdf
    monkeypatch.setattr(backend_api, "convert_to_pdf", convert_docx_text_to_pdf)

    api = BackendApi()
    if not api.init_pullenti():
        pytest.skip("Pullenti not available")

    from ocr_backend import create_ocr_backend, OCRBackendUnavailable
    try:
        create_ocr_backend(language="rus")
    except OCRBackendUnavailable:
        pytest.skip("OCR backend is not installed on this machine")

    # Never probe a developer's real documents, even as an optional fixture.
    sample_img = tmp_path / "synthetic_sample.png"
    from PIL import Image, ImageDraw
    img = Image.new("RGB", (800, 200), color=(255, 255, 255))
    draw = ImageDraw.Draw(img)
    draw.text((20, 20), "SYNTHETIC TEST DOCUMENT 123456", fill=(0, 0, 0), font_size=32)
    img.save(str(sample_img))

    out_dir = tmp_path / "img_out"
    out_dir.mkdir()
    api.save_original = True
    api.save_docx = True
    api.save_pdf = True
    api.save_markdown = True

    count = api.process_single_file(str(sample_img), set(), set(), output_dir=out_dir)
    assert count >= 0
    created = [f.name for f in out_dir.iterdir() if f.is_file() and not f.name.startswith(".")]
    assert any(f.endswith(".docx") for f in created)
    assert any(f.endswith(".pdf") for f in created)
    assert any(f.endswith(".md") for f in created)


@pytest.mark.parametrize("image_ext", [".jpg", ".jpeg"])
def test_image_with_zero_replacements_still_publishes_valid_pdf(tmp_path, monkeypatch, image_ext):
    """A readable image must produce its PDF even when NER finds no entities."""
    import backend_api
    from ocr_backend import OCRDocumentResult, OCRPageResult, OCRTextRegion
    from pdf_convert import convert_docx_text_to_pdf
    from pypdf import PdfReader

    api = BackendApi()
    if not api.init_pullenti():
        pytest.skip("Pullenti not available")
    api.save_original = False
    api.save_docx = False
    api.save_pdf = False
    api.save_markdown = False
    api.save_decoder = False
    api.save_log_files = False
    api.save_audit_files = False
    api.open_output_folder = False
    monkeypatch.setattr(backend_api, "convert_docx_text_to_pdf", convert_docx_text_to_pdf)
    monkeypatch.setattr(
        "ocr_backend.ocr_image_to_result",
        lambda path, **kwargs: OCRDocumentResult(
            text="Обычный текст без персональных данных и номеров",
            pages=(OCRPageResult(
                page=1, width=800, height=200,
                regions=(OCRTextRegion("Обычный текст без персональных данных и номеров"),),
            ),),
            backend="mock_readable",
        ),
    )
    from PIL import Image
    source = tmp_path / f"readable{image_ext}"
    Image.new("RGB", (800, 200), "white").save(source, format="JPEG")
    output = tmp_path / "output"
    output.mkdir()

    replacements = api.process_single_file(str(source), set(), set(), output_dir=output)

    assert replacements == 0
    pdf_outputs = [path for path in output.iterdir() if path.is_file() and path.suffix == ".pdf"]
    assert len(pdf_outputs) == 1
    reader = PdfReader(str(pdf_outputs[0]))
    assert len(reader.pages) == 1
    extracted = (reader.pages[0].extract_text() or "").replace("\xa0", " ")
    assert "Обычный текст без персональных данных и номеров" in extracted


def test_default_same_format_saving(tmp_path, monkeypatch):
    """По умолчанию (все тумблеры выключены) сохраняется исходный формат (docx->docx, pdf->pdf, xlsx->xlsx, img->docx)."""
    import backend_api
    from ocr_backend import OCRDocumentResult, OCRPageResult, OCRTextRegion
    from openpyxl import Workbook
    from pdf_convert import convert_docx_text_to_pdf
    monkeypatch.setattr(backend_api, "convert_to_pdf", convert_docx_text_to_pdf)
    # Этот тест проверяет набор создаваемых форматов, а не качество конкретного
    # OCR-движка. Делаем вход OCR детерминированным, чтобы он не зависел от
    # шрифта Pillow или доступности Apple Vision в тестовой сессии.
    monkeypatch.setattr(
        "ocr_backend.ocr_image_to_result",
        lambda path, **kwargs: OCRDocumentResult(
            text="ООО Вектор Тест",
            pages=(OCRPageResult(
                page=1,
                width=600,
                height=150,
                regions=(OCRTextRegion("ООО Вектор Тест"),),
            ),),
            backend="mock_readable",
        ),
    )

    api = BackendApi()
    if not api.init_pullenti():
        pytest.skip("Pullenti not available")

    api.save_original = False
    api.save_docx = False
    api.save_pdf = False
    api.save_markdown = False

    # 1. DOCX -> DOCX
    docx_file = tmp_path / "doc.docx"
    d = Document()
    d.add_paragraph("Тестовый договор ООО Ромашка")
    d.save(str(docx_file))

    out_docx = tmp_path / "out_default_docx"
    out_docx.mkdir()
    api.process_single_file(str(docx_file), set(), set(), output_dir=out_docx)
    files_docx = [f.name for f in out_docx.iterdir() if f.is_file() and not f.name.startswith(".")]
    assert any(f.endswith(".docx") for f in files_docx)
    assert not any(f.endswith(".pdf") for f in files_docx)
    assert not any(f.endswith(".xlsx") for f in files_docx)

    # 2. XLSX -> XLSX
    xlsx_file = tmp_path / "table.xlsx"
    wb = Workbook()
    ws = wb.active
    ws["A1"] = "Иванов Иван Иванович"
    wb.save(str(xlsx_file))

    out_xlsx = tmp_path / "out_default_xlsx"
    out_xlsx.mkdir()
    api.process_single_file(str(xlsx_file), set(), set(), output_dir=out_xlsx)
    files_xlsx = [f.name for f in out_xlsx.iterdir() if f.is_file() and not f.name.startswith(".")]
    assert any(f.endswith(".xlsx") for f in files_xlsx)
    assert not any(f.endswith(".docx") for f in files_xlsx)
    assert not any(f.endswith(".pdf") for f in files_xlsx)

    # 3. PDF -> PDF
    pdf_file = tmp_path / "doc.pdf"
    convert_docx_text_to_pdf(docx_file, pdf_file)

    out_pdf = tmp_path / "out_default_pdf"
    out_pdf.mkdir()
    api.process_single_file(str(pdf_file), set(), set(), output_dir=out_pdf)
    files_pdf = [f.name for f in out_pdf.iterdir() if f.is_file() and not f.name.startswith(".")]
    assert any(f.endswith(".pdf") for f in files_pdf)
    assert not any(f.endswith(".docx") for f in files_pdf)

    # 4. Image -> PDF
    from PIL import Image, ImageDraw
    img_file = tmp_path / "photo.png"
    img = Image.new("RGB", (600, 150), color=(255, 255, 255))
    draw = ImageDraw.Draw(img)
    draw.text((10, 10), "ООО Вектор Тест", fill=(0, 0, 0))
    img.save(str(img_file))

    out_img = tmp_path / "out_default_img"
    out_img.mkdir()
    api.process_single_file(str(img_file), set(), set(), output_dir=out_img)
    files_img = [f.name for f in out_img.iterdir() if f.is_file() and not f.name.startswith(".")]
    assert any(f.endswith(".pdf") for f in files_img)
    assert not any(f.endswith(".docx") for f in files_img)

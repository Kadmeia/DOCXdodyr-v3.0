from openpyxl import Workbook
import pytest

from backend_api import BackendApi


def test_xlsx_cell_failure_fails_closed_without_output(tmp_path, monkeypatch):
    source = tmp_path / "source.xlsx"
    workbook = Workbook()
    workbook.active.title = "Личная информация Иванов Иванович"
    workbook.active["A1"] = "Иванов Иван Иванович"
    workbook.save(source)
    output_dir = tmp_path / "output"

    def fail_targeted_cell(*_args, **_kwargs):
        raise ValueError("synthetic analysis failure")

    monkeypatch.setattr("xlsx_semantic.targeted_cell", fail_targeted_cell)
    api = BackendApi(lazy_pullenti=True)
    api.save_docx = False
    api.save_pdf = False
    api.save_markdown = False
    with pytest.raises(RuntimeError, match="безопасно обработать ячейку Excel") as failure:
        api.process_single_file(str(source), set(), set(), output_dir=output_dir)
    assert "Личная информация" not in str(failure.value)
    assert not list(output_dir.glob("*_cleaned.xlsx"))

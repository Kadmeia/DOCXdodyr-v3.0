from pathlib import Path
from docx import Document
from backend_api import BackendApi


def test_optional_processing_log_contains_only_aggregates(tmp_path):
    sentinel='SYNTHETIC_PRIVATE_SENTINEL_314159'
    source=tmp_path/'synthetic.docx'
    doc=Document();doc.add_paragraph(sentinel+' Иванов Иван Иванович.');doc.save(source)
    api=BackendApi();api.save_log_files=True;api.save_original=True;api.save_decoder=False
    try:
        assert api.process_single_file(str(source),set(),{})>0
        logs=list(tmp_path.glob('*_cleaned_log*.txt'))
        assert logs, 'Expected requested processing log'
        for log in logs:
            text=log.read_text()
            assert sentinel not in text
            assert 'Иванов' not in text
            assert 'docxdodyr.processing-log/v1' in text
    finally:api.shutdown()

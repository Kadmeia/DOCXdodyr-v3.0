"""Deterministic contracts for human-like manual-review actions."""

from pathlib import Path

from backend_api import BackendApi, build_ocr_span_map, ocr_position_for_span
from ocr_backend import OCRDocumentResult, OCRPageResult, OCRTextRegion
from privacy_audit import ReviewFinding, ReviewQueue


def _api_with_queue():
    api = BackendApi.__new__(BackendApi)
    api.review_queue = ReviewQueue([
        ReviewFinding("contract.docx", "Док Пар.0:text:0-10", "FIO", "[ФИО_1]", .92,
                      redacted_context="Подписант: [ФИО_1]", position={"scope": "body", "kind": "paragraph", "index": 0}),
        ReviewFinding("table.xlsx", "Excel:Лист1!B2:text:0-10", "PHONE", "[ТЕЛЕФОН_1]", .61,
                      redacted_context="Телефон: [ТЕЛЕФОН_1]", position={"scope": "sheet", "kind": "cell", "sheet": "Лист1", "cell": "B2"}),
        ReviewFinding("scan.pdf", "PDF:page:0", "ADDRESS", "[АДРЕС_1]", .88,
                      redacted_context="Адрес: [АДРЕС_1]", position={"scope": "pdf", "kind": "ocr", "page": 0, "bbox": [0.1, .2, .3, .1]}),
    ])
    return api


def test_review_filters_are_safe_and_do_not_mutate_queue():
    api = _api_with_queue()
    payload = api.get_review_findings(entity_type="FIO", min_confidence=90)
    assert [item["entity_type"] for item in payload["items"]] == ["FIO"]
    assert payload["filtered_summary"]["total"] == 1
    assert api.review_queue.summary()["pending"] == 3


def test_batch_accept_changes_only_filtered_pending_items():
    api = _api_with_queue()
    result = api.batch_decide_review_findings([], "accepted", document_ref=".pdf", min_confidence=80)
    assert result["count"] == 1
    assert result["changed"][0]["document_ref"] == "scan.pdf"
    assert api.review_queue.summary() == {"pending": 2, "accepted": 1, "rejected": 0, "skipped": 0, "total": 3}


def test_preview_contract_contains_geometry_but_never_context():
    api = _api_with_queue()
    finding_id = api.review_queue.items()[2].finding_id
    preview = api.get_review_finding_preview(finding_id)
    assert preview["position"] == {"kind": "ocr", "page": 0, "bbox": [.1, .2, .3, .1], "scope": "pdf"}
    assert "redacted_context" not in preview and "original" not in preview


def test_real_ocr_region_reaches_review_geometry_and_confidence():
    result = OCRDocumentResult(
        text="Преамбула\nИванов Иван Иванович",
        pages=(OCRPageResult(
            page=2, width=1200, height=1800,
            regions=(OCRTextRegion(
                "Иванов Иван Иванович", page=2,
                bbox=(0.1, 0.2, 0.7, 0.3), confidence=0.83,
            ),),
        ),), backend="fake",
    )
    span_map = build_ocr_span_map(result.text, result)
    start = result.text.index("Иванов")
    position, confidence = ocr_position_for_span(
        span_map, start, start + 6, {"scope": "pdf", "page": 0},
    )
    assert position["page"] == 1
    assert position["bbox"] == [0.1, 0.2, 0.6, 0.09999999999999998]
    assert position["width"] == 1200 and position["height"] == 1800
    assert confidence == 0.83


def test_review_screen_contract_supports_human_actions_and_safe_svg_preview():
    web_root = Path(__file__).parents[1] / "web"
    html = (web_root / "index.html").read_text(encoding="utf-8")
    script = (web_root / "script.js").read_text(encoding="utf-8")
    for element_id in ("review-filter-type", "review-filter-document", "review-filter-status", "review-filter-confidence", "review-batch-accept", "review-filter-reset"):
        assert f'id="{element_id}"' in html
    assert "batch_decide_review_findings" in script
    assert "appendSafeOcrPreview" in script and "createElementNS" in script
    assert "event.key === 'Escape'" in script
    assert "context.textContent" in script and "context.innerHTML" not in script

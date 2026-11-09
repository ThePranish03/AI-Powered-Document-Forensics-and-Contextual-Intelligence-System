import io
import json
from dataclasses import replace

import pytest
from pypdf import PdfReader

from app.core.config import settings
from app.services import dossier_narrative
from app.services.dossier_report import build_snapshot, render_html, render_pdf


def test_pdf_is_openable_and_contains_report_sections():
    snapshot = build_snapshot(
        investigation={"id": "case-id", "case_number": "CASE-1", "title": "Invoice review", "status": "in_review"},
        documents=[{"id": "doc-1", "filename": "invoice.png", "sha256": "a" * 64, "size_bytes": 10,
                    "processing_status": "ocr_completed", "uploaded_at": "2026-11-09T00:00:00+00:00",
                    "extracted_text": "Total 100.00", "extracted_blocks": [{"block_id": "b1", "page_index": 0, "text": "Total 100.00", "engine_bbox": [1, 2, 3, 4]}]}],
        findings=[], timeline=[], nodes=[], correlations=[], discrepancies=[],
        visual_runs=[{"id": "visual-run-1", "document_id": "doc-1", "status": "completed", "result": {
            "checks": [{"check_type": "font_baseline", "status": "executed", "algorithm": "PyMuPDF span comparison"}],
            "observations": [{"finding_id": "visual-finding-1", "page_number": 1, "regions": [{"x0": 1, "y0": 2, "x1": 3, "y1": 4}], "metrics": {"font_size_pt": 4}}],
        }}],
    )
    snapshot["report"] = {"dossier_id": "dossier-id", "version": 1, "created_by_user_id": "actor-id", "integrity_sha256": "b" * 64}
    snapshot["extracted_information"] = [{"document_id": "doc-1", "filename": "invoice.png", "text": "Total 100.00", "blocks": snapshot["evidence_inventory"][0]["extracted_blocks"]}]
    snapshot["relationship_overview"] = [{"label": "INV-1", "type": "INVOICE", "document_ids": ["doc-1"], "review_status": "candidate", "basis": "Exact OCR identifier."}]
    snapshot["timeline"] = [{"raw_date": "2024-01-01", "description": "Invoice dated", "confidence": 0.9, "evidence_refs": [{"document_id": "doc-1", "filename": "invoice.png", "page_number": 1, "block_id": "b1", "quoted_text": "Total 100.00"}]}]
    html = render_html(snapshot)
    assert "Document inventory and chain of custody" in html
    assert "Cross-document verification" in html
    assert "font_baseline" in html and "visual-run-1" in json.dumps(snapshot["visual_analysis"])
    pdf = render_pdf(snapshot)
    assert pdf.startswith(b"%PDF-")
    text = "\n".join(page.extract_text() or "" for page in PdfReader(io.BytesIO(pdf)).pages)
    for expected in ("Executive summary", "Document inventory and chain of custody", "OCR-extracted data", "Visual-forensics results", "font_baseline", "Relationship analysis", "Investigation timeline", "Audit trail", "Final conclusion"):
        assert expected in text
    assert "Total 100.00" in text and "block b1" in text


@pytest.mark.asyncio
async def test_cloudflare_narrative_rejects_unresolvable_citations(monkeypatch):
    monkeypatch.setattr(dossier_narrative, "settings", replace(
        settings,
        xai_api_key="test-token",
        xai_base_url="https://api.cloudflare.com/client/v4/accounts/test-account/ai/v1",
        xai_model="xai/grok-4.7",
    ))
    captured = {}

    def fake_urlopen(request, timeout):
        captured["url"] = request.full_url
        captured["body"] = json.loads(request.data)
        response = {"choices": [{"message": {"content": json.dumps({
            "summary": "An invoice shows Total 100.00.",
            "evidence_refs": [{"document_id": "doc-1", "page_number": 1, "block_id": "b1", "quoted_text": "Total 100.00"}],
        })}}]}
        return io.BytesIO(json.dumps(response).encode("utf-8"))

    monkeypatch.setattr(dossier_narrative.urllib.request, "urlopen", fake_urlopen)
    result = await dossier_narrative.generate_grounded_narrative({
        "investigation": {"title": "Invoice review"}, "findings": [],
        "cross_document_links": [], "cross_document_discrepancies": [],
        "evidence_inventory": [{"id": "doc-1", "filename": "invoice.png", "extracted_blocks": [
            {"block_id": "b1", "page_index": 0, "text": "Total 100.00"},
        ]}],
    })
    assert captured["url"].endswith("/chat/completions")
    assert captured["body"]["model"] == "xai/grok-4.7"
    assert result["status"] == "generated"
    assert result["evidence_refs"][0]["quoted_text"] == "Total 100.00"

    def fabricated_citation_urlopen(request, timeout):
        response = {"choices": [{"message": {"content": json.dumps({
            "summary": "An invoice shows Total 100.00.",
            "evidence_refs": [{"document_id": "doc-1", "page_number": 9, "block_id": "made-up", "quoted_text": "Total 100.00"}],
        })}}]}
        return io.BytesIO(json.dumps(response).encode("utf-8"))

    monkeypatch.setattr(dossier_narrative.urllib.request, "urlopen", fabricated_citation_urlopen)
    result = await dossier_narrative.generate_grounded_narrative({
        "investigation": {}, "findings": [], "cross_document_links": [], "cross_document_discrepancies": [],
        "evidence_inventory": [{"id": "doc-1", "filename": "invoice.png", "extracted_blocks": [{"block_id": "b1", "page_index": 0, "text": "Total 100.00"}]}],
    })
    assert result["status"] == "omitted"
    assert result["text"] is None

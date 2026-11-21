import io
import json
from dataclasses import replace

import pytest
from pypdf import PdfReader

from app.core.config import settings
from app.services import dossier_narrative
from app.services.dossier_report import build_snapshot, render_html, render_pdf


@pytest.mark.parametrize(("runs", "expected"), [
    ([], "not_run"),
    ([{"id": "r-complete", "document_id": "d1", "source_sha256": "a" * 64, "status": "completed", "analyzer_version": "v1", "model_version": "checkpoint-v1", "result": {"checks": [{"check_type": "trufor_engine", "status": "executed", "algorithm": "TruFor worker", "metrics": {"pages_completed": 1}}]}, "pages": [{"page_number": 1, "status": "completed", "integrity_score": 0.72, "integrity_meaning": "model output; not fraud probability", "localization_artifact_id": "loc-1", "reliability_artifact_id": "rel-1", "regions": [{"region_index": 0, "bbox_pixel": {"x0": 1, "y0": 2, "x1": 3, "y1": 4}, "bbox_pdf_points": {"x0": 1, "y0": 2, "x1": 3, "y1": 4}, "review_status": "accepted", "linked_ocr_block_ids": ["b1"]}]}]}], "completed"),
    ([{"id": "r-ok", "status": "completed", "result": {"checks": []}}, {"id": "r-failed", "status": "failed", "result": {"checks": []}}], "partial"),
    ([{"id": "r-failed", "status": "failed", "result": {"checks": []}}], "failed"),
    ([{"id": "r-unavailable", "status": "unavailable", "result": {"checks": []}}], "unavailable"),
    ([{"id": "r-processing", "status": "processing", "result": {"checks": []}}], "running"),
])
def test_visual_analysis_status_and_details_are_snapshotted(runs, expected):
    snapshot = build_snapshot(
        investigation={"id": "case-id", "case_number": "CASE-1", "title": "Review", "status": "in_review"},
        documents=[], findings=[], timeline=[], nodes=[], correlations=[], discrepancies=[], visual_runs=runs,
    )
    assert snapshot["visual_analysis"]["status"] == expected
    rendered = render_html(snapshot)
    assert f">{expected}<" in rendered
    if expected == "completed":
        assert "a" * 64 in rendered
        assert "Integrity output" in rendered
        assert "Localization artifact" in rendered
        assert "accepted" in rendered and "b1" in rendered
        assert "not fraud probability" in rendered
        pdf_text = "\n".join(page.extract_text() or "" for page in PdfReader(io.BytesIO(render_pdf(snapshot))).pages)
        assert "integrity output" in pdf_text
        assert "accepted" in pdf_text and "b1" in pdf_text


def test_pdf_is_openable_and_contains_report_sections():
    snapshot = build_snapshot(
        investigation={"id": "case-id", "case_number": "CASE-1", "title": "Invoice review", "status": "in_review"},
        documents=[{"id": "doc-1", "filename": "invoice.png", "sha256": "a" * 64, "size_bytes": 10,
                    "processing_status": "ocr_completed", "uploaded_at": "2026-11-09T00:00:00+00:00",
                    "extracted_text": "Total 100.00", "extracted_blocks": [{"block_id": "b1", "page_index": 0, "text": "Total 100.00", "engine_bbox": [1, 2, 3, 4]}],
                    "extraction_coverage": {"page_count": 1, "ocr_blocks": 1, "native_pdf_blocks": 2, "pages_with_ocr_text": 1, "pages_with_native_text": 1}}],
        findings=[], timeline=[], nodes=[], correlations=[], discrepancies=[],
        visual_runs=[{"id": "visual-run-1", "document_id": "doc-1", "status": "completed", "result": {
            "checks": [{"check_type": "font_baseline", "status": "executed", "algorithm": "PyMuPDF span comparison"}],
            "observations": [{"finding_id": "visual-finding-1", "check_type": "font_baseline", "page_number": 1, "regions": [{"x0": 1, "y0": 2, "x1": 3, "y1": 4}], "metrics": {"font_size_pt": 4}}],
        }}],
    )
    snapshot["report"] = {"dossier_id": "dossier-id", "version": 1, "created_by_user_id": "actor-id", "integrity_sha256": "b" * 64}
    snapshot["extracted_information"] = [{"document_id": "doc-1", "filename": "invoice.png", "text": "Total 100.00", "blocks": snapshot["evidence_inventory"][0]["extracted_blocks"], "coverage": snapshot["evidence_inventory"][0]["extraction_coverage"]}]
    snapshot["relationship_overview"] = [{"label": "INV-1", "type": "INVOICE", "document_ids": ["doc-1"], "review_status": "candidate", "basis": "Exact OCR identifier."}]
    snapshot["timeline"] = [{"raw_date": "2024-01-01", "description": "Invoice dated", "confidence": 0.9, "evidence_refs": [{"document_id": "doc-1", "filename": "invoice.png", "page_number": 1, "block_id": "b1", "quoted_text": "Total 100.00"}]}]
    html = render_html(snapshot)
    assert "Document inventory and chain of custody" in html
    assert "Cross-document verification" in html
    assert "Extraction coverage: Pages: 1" in html and "Native PDF blocks: 2" in html
    assert "font_baseline" in html and "visual-run-1" in json.dumps(snapshot["visual_analysis"])
    pdf = render_pdf(snapshot)
    assert pdf.startswith(b"%PDF-")
    text = "\n".join(page.extract_text() or "" for page in PdfReader(io.BytesIO(pdf)).pages)
    for expected in ("Executive summary", "Document inventory and chain of custody", "OCR-extracted data", "Visual-forensics results", "font_baseline", "Relationship analysis", "Investigation timeline", "Audit trail", "Final conclusion", "Native PDF blocks: 2"):
        assert expected in text
    assert "Total 100.00" in text and "block b1" in text


def test_pdf_structural_analysis_is_included_in_versioned_exports():
    pdf_run = {
        "id": "pdf-run-1", "document_id": "doc-1", "source_sha256": "c" * 64,
        "status": "completed", "parser": {"name": "PyMuPDF", "version": "1.26.7"},
        "created_at": "2026-11-20T00:00:00+00:00",
        "result": {
            "document": {"page_count": 1, "revision_count": 2, "xref_entry_count": 10,
                         "embedded_images": [{"object_number": 8, "sha256": "d" * 64}],
                         "signature_objects": [{"object_number": 9, "status": "present_unverified"}],
                         "embedded_file_count": 0, "javascript_reference_count": 0, "metadata": {"title": "Synthetic"}},
            "objects": {"type_counts": {"Page": 1}},
            "pages": [{"page_number": 1, "text_spans": [{"text": "Total 100.00"}]}],
            "signature_verification": {"library": "pyHanko", "status": "no_signatures", "signatures": [], "limitations": []},
            "revision_inventory": {"status": "completed", "revisions": [{"revision_number": 1, "changes": {"added": [1, 2]}}]},
            "limitations": ["Signature integrity was checked with pyHanko; no trust roots were configured."],
        },
    }
    snapshot = build_snapshot(
        investigation={"id": "case-id", "case_number": "CASE-1", "title": "PDF review", "status": "in_review"},
        documents=[], findings=[], timeline=[], nodes=[], correlations=[], discrepancies=[], pdf_runs=[pdf_run],
    )

    assert snapshot["pdf_structural_analysis"]["status"] == "completed"
    assert snapshot["pdf_structural_analysis"]["runs"][0]["result"]["pages"][0]["text_spans"][0]["text"] == "Total 100.00"
    assert "PyMuPDF" in render_html(snapshot)
    assert "present_unverified" in json.dumps(snapshot["pdf_structural_analysis"])
    pdf_text = "\n".join(page.extract_text() or "" for page in PdfReader(io.BytesIO(render_pdf(snapshot))).pages)
    assert "PDF structural analysis" in pdf_text
    assert "2" in pdf_text and "revision" in pdf_text.lower()
    assert "no_signatures" in pdf_text


def test_pdf_visual_observations_do_not_create_unbreakable_table_rows():
    snapshot = build_snapshot(
        investigation={"id": "case-id", "case_number": "CASE-1", "title": "Synthetic review", "status": "in_review"},
        documents=[], findings=[], timeline=[], nodes=[], correlations=[], discrepancies=[],
    )
    reference = {
        "document_id": "doc-1",
        "filename": "SYNTHETIC_Apex_Invoice.pdf",
        "page_number": 1,
        "block_id": None,
        "quoted_text": "Synthetic small-type review marker · generated solely for this demo",
        "bounding_box": {"x0": 52.0, "y0": 365.7, "x1": 170.93, "y1": 371.2},
    }
    observations = [{
        "finding_type": "visual_font_size_outlier",
        "check_type": "font_baseline",
        "page_number": 1,
        "regions": [reference["bounding_box"]],
        "metrics": {"font": "Helvetica", "font_size_pt": 4.0, "page_median_font_size_pt": 11.0},
        "evidence_ref": reference,
    } for _ in range(30)]
    snapshot["visual_analysis"]["checks"] = [{
        "check_id": "font_baseline",
        "method": "PyMuPDF span font-size peer comparison",
        "status": "executed",
        "measurements": {"text_spans_inspected": 15, "font_outliers": 1},
        "result": observations,
        "source_references": [reference],
        "diagnostics": {"pages_analyzed": [1], "skipped": []},
        "limitations": "Footnotes and intentional typographic hierarchy may explain the difference.",
    }]

    pdf = render_pdf(snapshot)

    assert pdf.startswith(b"%PDF-")
    text = "\n".join(page.extract_text() or "" for page in PdfReader(io.BytesIO(pdf)).pages)
    assert "Synthetic small-type review marker" in text
    assert "SYNTHETIC_Apex_Invoice.pdf" in text
    assert "font_size_pt" in text


def test_groq_narrative_accepts_exact_citations_and_rejects_unresolvable_ones(monkeypatch):
    monkeypatch.setattr(dossier_narrative, "settings", replace(
        settings,
        llm_api_key="synthetic-test-token",
        llm_base_url="https://api.groq.com/openai/v1",
        llm_model="openai/gpt-oss-120b",
    ))
    captured = {}

    def fake_chat_completion(messages, **kwargs):
        captured["messages"] = messages
        captured["kwargs"] = kwargs
        return {"model": "openai/gpt-oss-120b", "usage": {}, "content": json.dumps({
            "summary": "An invoice shows Total 100.00.",
            "evidence_refs": [{"document_id": "doc-1", "page_number": 1, "block_id": "b1", "quoted_text": "Total 100.00"}],
        })}

    monkeypatch.setattr(dossier_narrative, "chat_completion", fake_chat_completion)
    result = dossier_narrative._request({
        "investigation": {"title": "Invoice review"}, "findings": [],
        "cross_document_links": [], "cross_document_discrepancies": [],
        "evidence_inventory": [{"id": "doc-1", "filename": "invoice.png", "extracted_blocks": [
            {"block_id": "b1", "page_index": 0, "text": "Total 100.00"},
        ]}],
    })
    assert captured["kwargs"]["response_format"] == {"type": "json_object"}
    assert captured["kwargs"]["timeout"] == settings.llm_request_timeout_seconds
    assert "Total 100.00" in json.dumps(captured["messages"])
    assert result["model"] == "openai/gpt-oss-120b"
    assert result["status"] == "generated"
    assert result["evidence_refs"][0]["quoted_text"] == "Total 100.00"

    def fabricated_citation_chat(messages, **kwargs):
        return {"model": "openai/gpt-oss-120b", "usage": {}, "content": json.dumps({
            "summary": "An invoice shows Total 100.00.",
            "evidence_refs": [{"document_id": "doc-1", "page_number": 9, "block_id": "made-up", "quoted_text": "Total 100.00"}],
        })}

    monkeypatch.setattr(dossier_narrative, "chat_completion", fabricated_citation_chat)
    result = dossier_narrative._request({
        "investigation": {}, "findings": [], "cross_document_links": [], "cross_document_discrepancies": [],
        "evidence_inventory": [{"id": "doc-1", "filename": "invoice.png", "extracted_blocks": [{"block_id": "b1", "page_index": 0, "text": "Total 100.00"}]}],
    })
    assert result["status"] == "omitted"
    assert result["text"] is None


def test_dossier_narrative_reports_missing_groq_key(monkeypatch):
    monkeypatch.setattr(dossier_narrative, "settings", replace(
        settings, llm_api_key="",
    ))

    result = dossier_narrative._request({
        "evidence_inventory": [{"id": "doc-1", "extracted_blocks": [{"block_id": "b1", "page_index": 0, "text": "safe synthetic text"}]}],
    })

    assert result["status"] == "unavailable"
    assert result["text"] is None
    assert result["reason"] == "GROQ_API_KEY is not configured."


def test_dossier_narrative_safely_reports_provider_request_failure(monkeypatch):
    monkeypatch.setattr(dossier_narrative, "settings", replace(
        settings, llm_api_key="synthetic-test-token",
    ))

    def fail_request(_messages, **_kwargs):
        from app.services.llm_client import LLMRequestError
        raise LLMRequestError("synthetic transport failure")

    monkeypatch.setattr(dossier_narrative, "chat_completion", fail_request)
    result = dossier_narrative._request({
        "evidence_inventory": [{"id": "doc-1", "extracted_blocks": [{"block_id": "b1", "page_index": 0, "text": "safe synthetic text"}]}],
    })

    assert result["status"] == "failed"
    assert result["text"] is None
    assert result["evidence_refs"] == []


def test_dossier_narrative_rejects_invalid_groq_json(monkeypatch):
    monkeypatch.setattr(dossier_narrative, "settings", replace(
        settings, llm_api_key="synthetic-test-key",
    ))
    monkeypatch.setattr(dossier_narrative, "chat_completion", lambda *_args, **_kwargs: {
        "content": "not-json", "model": "openai/gpt-oss-120b", "usage": {},
    })

    result = dossier_narrative._request({
        "evidence_inventory": [{"id": "doc-1", "extracted_blocks": [{"block_id": "b1", "page_index": 0, "text": "safe synthetic text"}]}],
    })

    assert result["status"] == "failed"
    assert result["text"] is None
    assert result["evidence_refs"] == []

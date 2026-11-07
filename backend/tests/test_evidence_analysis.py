from dataclasses import replace
from datetime import datetime, timezone
from types import SimpleNamespace
from uuid import UUID

import pytest
from sqlalchemy import select

from app.api.v1 import auth as auth_routes
from app.api.v1 import investigations as investigation_routes
from app.core.config import settings
from app.main import app
from app.models.entities import Document, OCRResult
from app.services.audit_chain import event_hash, verify_audit_chain
from app.services.ocr_metrics import analyze_documents, character_error_rate, detection_scores, evaluate_samples, extract_document_facts, word_error_rate


PASSWORD = "StrongPassword123"


def test_error_rates_and_empty_ground_truth():
    assert character_error_rate("cat", "cat")["value"] == 0
    assert word_error_rate("total 100", "total 100")["value"] == 0
    changed = character_error_rate("abcd", "abXd")
    assert changed["value"] == 0.25
    empty = character_error_rate("", "text")
    assert empty["value"] is None
    assert empty["status"] == "unavailable"
    assert word_error_rate("", "")["status"] == "unavailable"
    detection = detection_scores([[0, 0, 10, 10]], [[0, 0, 10, 10]])
    assert detection["f1"] == 1
    missing = detection_scores(None, [[0, 0, 1, 1]])
    assert missing["status"] == "unavailable"
    report = evaluate_samples([{"id": "s", "ground_truth": "", "hypothesis": ""}])
    assert report["exact_match_accuracy"] == 1
    assert report["cer_mean"] is None
    assert report["latency"]["status"] == "unavailable"


def test_date_extraction_rejects_impossible_calendar_dates():
    facts = extract_document_facts({
        "document_id": "doc-1",
        "blocks": [{
            "block_id": "block-1",
            "page_index": 0,
            "text": "2023-02-29 2024-02-29 2024-04-31 2024-04-30",
        }],
    })
    assert [item["raw"] for item in facts["dates"]] == ["2024-02-29", "2024-04-30"]


def test_audit_chain_verification_detects_changed_event_payload():
    event_id = UUID("3f6a3d8d-e6e0-4ed1-bb88-5ad18f919f8d")
    investigation_id = UUID("cfd47191-a4c5-45a9-a70d-f24b6a36cda4")
    created_at = datetime(2026, 1, 2, tzinfo=timezone.utc)
    payload = {
        "id": str(event_id),
        "investigation_id": str(investigation_id),
        "actor_user_id": None,
        "action": "evidence.uploaded",
        "target_type": "document",
        "target_id": "doc-1",
        "details": {"filename": "source.png"},
        "created_at": created_at.isoformat(),
    }
    event = SimpleNamespace(
        **{**payload, "created_at": created_at},
        previous_hash="genesis",
        event_hash=event_hash("genesis", payload),
    )
    assert verify_audit_chain([event])["valid"] is True
    event.details = {"filename": "changed.png"}
    result = verify_audit_chain([event])
    assert result["valid"] is False
    assert result["broken_event_id"] == str(event_id)


def test_extraction_gaps_are_not_reported_as_visual_forensics():
    result = analyze_documents([{
        "document_id": "doc-1",
        "filename": "scan.png",
        "provider": "test-ocr",
        "blocks": [],
        "text": "",
    }])
    assert [finding["finding_type"] for finding in result["findings"]] == ["extraction_gap"]


@pytest.mark.asyncio
async def test_analysis_review_dossier_and_evaluation(client, monkeypatch, tmp_path):
    auth_routes._attempts.clear()
    monkeypatch.setattr(auth_routes, "settings", replace(settings, self_registration_enabled=True))
    monkeypatch.setattr(investigation_routes, "settings", replace(settings, document_storage_path=str(tmp_path)))
    await client.post("/api/v1/auth/register", json={"full_name": "Test Investigator", "email": "analysis@example.com", "password": PASSWORD})
    case = await client.post("/api/v1/investigations", headers={"X-CSRF-Token": client.cookies.get("dfi_csrf")}, json={"title": "Conflicting invoices"})
    case_id = case.json()["id"]
    headers = {"X-CSRF-Token": client.cookies.get("dfi_csrf")}
    image = b"\x89PNG\r\n\x1a\n" + b"image-content"
    first = await client.post(f"/api/v1/investigations/{case_id}/documents", headers={**headers, "X-File-Name": "invoice-a.png", "Content-Type": "image/png"}, content=image)
    second = await client.post(f"/api/v1/investigations/{case_id}/documents", headers={**headers, "X-File-Name": "invoice-b.png", "Content-Type": "image/png"}, content=image + b"b")
    texts = {
        first.json()["id"]: "Invoice INV-10001\nSubtotal 100.00\nTax 10.00\nTotal 150.00\nDate 2024-01-15",
        second.json()["id"]: "Invoice INV-10001\nTotal 100.00\nDate 01/02/2024",
    }
    async with app.state.test_sessions() as session:
        for document_id, text in texts.items():
            document = await session.scalar(select(Document).where(Document.id == UUID(document_id)))
            document.processing_status = "ocr_completed"
            session.add(OCRResult(
                document_id=document.id,
                provider="test-ocr",
                provider_version="0",
                raw_result={"engine": "test"},
                blocks=[{"block_id": "block_000001", "text": text, "page_index": 0, "engine_confidence": 0.42, "engine_bbox": [1, 2, 30, 12]}],
                recognized_text=text,
            ))
        await session.commit()

    run = await client.post(f"/api/v1/investigations/{case_id}/analysis-runs", headers=headers)
    assert run.status_code == 201
    assert run.json()["candidate_findings_written"] >= 2
    assert run.json()["visual_forensics"]["status"] == "unavailable"
    assert run.json()["visual_forensics"]["available_signals"] == []
    findings = (await client.get(f"/api/v1/investigations/{case_id}/findings")).json()["items"]
    assert all(item["finding_type"] != "visual_extraction_gap" for item in findings)
    rerun = await client.post(f"/api/v1/investigations/{case_id}/analysis-runs", headers=headers)
    assert rerun.status_code == 201
    rerun_findings = (await client.get(f"/api/v1/investigations/{case_id}/findings")).json()["items"]
    assert len(rerun_findings) == len(findings)
    assert rerun.json()["candidate_findings_written"] == run.json()["candidate_findings_written"]
    findings = rerun_findings
    kinds = {item["finding_type"] for item in findings}
    assert "identifier_amount_conflict" in kinds
    assert "reconciliation" in kinds
    assert "ocr_quality" in kinds
    timeline = (await client.get(f"/api/v1/investigations/{case_id}/timeline")).json()["items"]
    ambiguous = [item for item in timeline if item["raw_date"] == "01/02/2024"]
    assert ambiguous and ambiguous[0]["ambiguous"] is True and ambiguous[0]["interpreted_date"] is None
    iso = [item for item in timeline if item["raw_date"] == "2024-01-15"]
    assert iso and iso[0]["interpreted_date"] == "2024-01-15" and iso[0]["timezone_known"] is False
    search = await client.post(f"/api/v1/investigations/{case_id}/search", headers=headers, json={"query": "INV-10001"})
    assert search.status_code == 200
    citation = next(item for item in search.json()["citations"] if item["kind"] == "ocr_block" and item["block_id"] == "block_000001")
    assert citation["page_number"] == 1

    conflict = next(item for item in findings if item["finding_type"] == "identifier_amount_conflict")
    assert all(ref["page_number"] == 1 for ref in conflict["evidence_refs"])
    review = await client.patch(
        f"/api/v1/investigations/{case_id}/findings/{conflict['id']}",
        headers=headers,
        json={"status": "needs_review", "review_notes": "Compare the two totals with the originals."},
    )
    assert review.status_code == 200
    assert review.json()["status"] == "needs_review"
    await client.post(f"/api/v1/investigations/{case_id}/analysis-runs", headers=headers)
    persisted_review = next(
        item for item in (await client.get(f"/api/v1/investigations/{case_id}/findings")).json()["items"]
        if item["id"] == conflict["id"]
    )
    assert persisted_review["status"] == "needs_review"
    assert persisted_review["review_notes"] == "Compare the two totals with the originals."

    graph = (await client.get(f"/api/v1/investigations/{case_id}/relationships/graph")).json()
    identifier = next(node for node in graph["nodes"] if node["label"] == "INV-10001")
    assert len(identifier["document_ids"]) == 2
    assert identifier["resolution"] == "identifier_match"

    dossier = await client.post(f"/api/v1/investigations/{case_id}/dossiers", headers=headers)
    assert dossier.status_code == 201
    assert "INV-10001" in dossier.json()["html"]
    assert "Not included" in dossier.json()["html"]
    audit = (await client.get(f"/api/v1/investigations/{case_id}/audit")).json()["items"]
    audit_response = await client.get(f"/api/v1/investigations/{case_id}/audit")
    assert audit_response.json()["verification"]["valid"] is True
    assert audit_response.json()["verification"]["verified_count"] == len(audit)
    hashes = {item["event_hash"] for item in audit}
    assert sum(item["previous_hash"] == "genesis" for item in audit) == 1
    assert all(item["previous_hash"] == "genesis" or item["previous_hash"] in hashes for item in audit)
    assert [item["action"] for item in audit].count("evidence.uploaded") == 2

    evaluation = await client.post(f"/api/v1/investigations/{case_id}/ocr-evaluations", headers=headers, json={
        "dataset_name": "tiny",
        "samples": [{"id": "s1", "ground_truth": "abcd", "hypothesis": "abXd"}],
    })
    assert evaluation.status_code == 201
    body = evaluation.json()
    assert body["metrics"]["cer_mean"] == 0.25
    assert body["metrics"]["detection"]["status"] == "unavailable"
    assert body["metrics"]["latency"]["status"] == "unavailable"

    outsider = await client.post("/api/v1/auth/register", json={"full_name": "Other", "email": "other-analysis@example.com", "password": PASSWORD})
    assert outsider.status_code == 201
    hidden = await client.get(f"/api/v1/investigations/{case_id}/findings")
    assert hidden.status_code == 404

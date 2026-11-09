from dataclasses import replace
from datetime import datetime, timezone
import io
from types import SimpleNamespace
from uuid import UUID

import pytest
from pypdf import PdfReader
from sqlalchemy import select

from app.api.v1 import auth as auth_routes
from app.api.v1 import investigations as investigation_routes
from app.core.config import settings
from app.main import app
from app.models.entities import DossierSnapshot, Document, OCRResult
from app.services import investigation_analysis as investigation_analysis_service
from app.services.audit_chain import event_hash, verify_audit_chain
from app.services.ocr_metrics import analyze_documents, build_cross_document_correlation, character_error_rate, detection_scores, evaluate_samples, extract_document_facts, word_error_rate


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


def test_amount_and_identifier_parser_handles_ocr_text_without_partial_values():
    docs = [
        {"document_id": "po", "filename": "po.pdf", "blocks": [
            {"block_id": "p1", "page_index": 0, "text": "Purchase Order PO-2041"},
            {"block_id": "p2", "page_index": 0, "text": "Approved purchase order total: 8o,ooo"},
            {"block_id": "p3", "page_index": 0, "text": "Date: 12 August 2026"},
        ]},
        {"document_id": "invoice", "filename": "invoice.pdf", "blocks": [
            {"block_id": "i1", "page_index": 0, "text": "Tax Invoice INV-778"},
            {"block_id": "i2", "page_index": 0, "text": "Invoice subtotal: ■85,000Tax: ■0\nInvoice total: ■85,000"},
            {"block_id": "i3", "page_index": 0, "text": "Reference PO: PO-2041"},
        ]},
    ]
    result = analyze_documents(docs)
    conflict = next(item for item in result["findings"] if item["finding_type"] == "identifier_amount_conflict")
    assert conflict["confidence"] == 0.45
    assert "8o,ooo => 80000.00" in conflict["description"]
    assert "85,000 => 85000.00" in conflict["description"]
    assert {ref["document_id"] for ref in conflict["evidence_refs"]} == {"po", "invoice"}
    assert all(ref["page_number"] == 1 and ref["block_id"] for ref in conflict["evidence_refs"])
    assert all(ref["normalization_warnings"] for ref in conflict["evidence_refs"] if ref["document_id"] == "po")
    extracted = result["documents"][0]["identifiers"]
    assert [(item["entity_type"], item["value"]) for item in extracted] == [("PURCHASE_ORDER", "PO-2041")]
    invoice = result["documents"][1]
    assert invoice["labeled_amounts"]["subtotal"]["value"] == 85000
    assert invoice["labeled_amounts"]["tax"]["value"] == 0
    assert invoice["labeled_amounts"]["total"]["value"] == 85000
    assert invoice["identifiers"][-1]["value"] == "PO-2041"
    assert result["timeline"][0]["interpreted_date"] == "2026-08-12"


OCR_CASE_FIXTURE = [
    ("01_Purchase_Order_PO-2041.pdf", [
        "Purchase Order PO-2041",
        "Company: Northstar Components Pvt. Ltd. | Date: 12 August 2026",
        "Supplier: Apex Industrial Supplies | Vendor ID: V-104",
        "Approved purchase order total: 8o,ooo",
        "Approval: Procurement Manager, R. Mehta",
        "Terms: Payment subject to invoice matching against this purchase order.",
    ]),
    ("02_Invoice_INV-778.pdf", [
        "Tax Invoice INV-778",
        "Supplier: Apex Industrial Supplies | Vendor ID: V-104",
        "Bill to: Northstar Components Pvt. Ltd. | Invoice date: 14 August 2026",
        "Invoice subtotal: ■85,000Tax: ■0\nInvoice total: ■85,000",
        "Reference PO: PO-2041",
        "Remittance bank account: 0098123456",
    ]),
    ("03_Approval_Email.pdf", [
        "Email — Invoice Approval",
        "From: R. Mehta, Procurement Manager To: Accounts Payable Date: 15 August 2026Subject: Approval — Apex invoice INV-778",
        "Please process Apex Industrial Supplies invoice INV-778 against PO-2041. The approved purchase order value is ■80,oo0. Any amount above the PO should be held for clarification before payment.",
        "Regards,R.Mehta",
    ]),
    ("04_Bank_Statement.pdf", [
        "Bank Statement — Northstar Components",
        "Account holder: Northstar Components Pvt. Ltd.Statement period: 16–31 August 2026",
        "Transaction reference: NEFT-20260820-4417",
        "Statement shows payment of 85,000 to Apex Industrial Supplies.",
    ]),
    ("05_Vendor_Ledger.pdf", [
        "Vendor Ledger — Apex Industrial Supplies",
        "Company: Northstar Components Pvt. Ltd.\nVendor ID: V-104\n\nPeriod: August 2026",
        "Ledger records invoice of 85,000 and settlement of 85,000.",
    ]),
]


async def _run_persisted_ocr_fixture(client, monkeypatch, tmp_path, *, email: str, consistent: bool):
    auth_routes._attempts.clear()
    monkeypatch.setattr(auth_routes, "settings", replace(settings, self_registration_enabled=True))
    monkeypatch.setattr(investigation_routes, "settings", replace(settings, document_storage_path=str(tmp_path)))
    await client.post("/api/v1/auth/register", json={"full_name": "Fixture Investigator", "email": email, "password": PASSWORD})
    headers = {"X-CSRF-Token": client.cookies.get("dfi_csrf")}
    case = await client.post("/api/v1/investigations", headers=headers, json={"title": "Stored OCR fixture"})
    assert case.status_code == 201
    case_id = case.json()["id"]
    docs = []
    for index, (filename, lines) in enumerate(OCR_CASE_FIXTURE):
        text = "\n".join(lines)
        if consistent:
            text = text.replace("8o,ooo", "80,000").replace("80,oo0", "80,000").replace("85,000", "80,000")
            lines = text.split("\n")
        uploaded = await client.post(
            f"/api/v1/investigations/{case_id}/documents",
            headers={**headers, "X-File-Name": filename, "Content-Type": "application/pdf"},
            content=b"%PDF-1.4\nfixture-" + str(index).encode() + b"\n%%EOF",
        )
        assert uploaded.status_code == 201
        docs.append((uploaded.json(), lines))
    async with app.state.test_sessions() as session:
        for uploaded, lines in docs:
            document = await session.scalar(select(Document).where(Document.id == UUID(uploaded["id"])))
            document.processing_status = "ocr_completed"
            session.add(OCRResult(
                document_id=document.id,
                provider="stored-fixture-ocr",
                provider_version="fixture-1",
                raw_result={"fixture": True},
                blocks=[{"block_id": f"block_{index + 1:06d}", "text": line, "page_index": 0} for index, line in enumerate(lines)],
                recognized_text="\n".join(lines),
            ))
        await session.commit()
    run = await client.post(f"/api/v1/investigations/{case_id}/analysis-runs", headers=headers)
    assert run.status_code == 201
    items = await client.get(f"/api/v1/investigations/{case_id}/findings")
    assert items.status_code == 200
    return case_id, run.json(), items.json()["items"]


@pytest.mark.asyncio
async def test_persisted_actual_ocr_fixture_finds_po_invoice_discrepancy(client, monkeypatch, tmp_path):
    case_id, run, findings = await _run_persisted_ocr_fixture(
        client, monkeypatch, tmp_path, email="stored-discrepancy@example.com", consistent=False,
    )
    assert run["documents_considered"] == 5
    assert run["documents_with_ocr"] == 5
    assert run["candidate_findings_written"] == 1
    assert run["diagnostics"]["summary"]["findings_produced"] == 1
    shared = next(rule for rule in run["diagnostics"]["rules"] if rule["rule_id"] == "shared_identifier_total_conflict")
    assert shared["rules_matched"] == 1
    assert shared["comparisons_performed"][0]["identifier"] == "PO-2041"
    assert len(findings) == 1
    finding = findings[0]
    assert finding["finding_type"] == "identifier_amount_conflict"
    assert finding["status"] == "candidate"
    assert "not proof" in finding["description"]
    assert {ref["filename"] for ref in finding["evidence_refs"]} == {
        "01_Purchase_Order_PO-2041.pdf", "02_Invoice_INV-778.pdf",
    }
    assert all(ref["document_id"] and ref["page_number"] == 1 and ref["block_id"] for ref in finding["evidence_refs"])
    assert (await client.get(f"/api/v1/investigations/{case_id}/findings")).json()["items"][0]["id"] == finding["id"]


@pytest.mark.asyncio
async def test_persisted_actual_ocr_fixture_with_matching_amounts_produces_no_finding(client, monkeypatch, tmp_path):
    _case_id, run, findings = await _run_persisted_ocr_fixture(
        client, monkeypatch, tmp_path, email="stored-consistent@example.com", consistent=True,
    )
    assert run["documents_considered"] == 5
    assert run["documents_with_ocr"] == 5
    assert run["candidate_findings_written"] == 0
    assert findings == []
    shared = next(rule for rule in run["diagnostics"]["rules"] if rule["rule_id"] == "shared_identifier_total_conflict")
    assert shared["rules_matched"] == 0
    assert any(item["reason"] == "labeled_totals_equal" for item in shared["rules_skipped"])


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


def test_cross_document_correlation_clusters_exact_identifier_with_all_citations():
    documents = [{
        "document_id": f"doc-{index}", "filename": f"invoice-{index}.png", "processing_status": "ocr_completed",
        "blocks": [{"block_id": f"b-{index}", "page_index": 0, "text": f"Invoice No: INV-88421\nTotal: {total}.00\nDate: 2024-04-01"}],
    } for index, total in enumerate((100, 100, 125), start=1)]
    result = build_cross_document_correlation(documents)
    assert result["summary"]["candidate_links"] == 1
    assert result["summary"]["contradictions"] == 1
    assert result["links"][0]["document_ids"] == ["doc-1", "doc-2", "doc-3"]
    assert {ref["document_id"] for ref in result["links"][0]["evidence_refs"]} == {"doc-1", "doc-2", "doc-3"}
    comparison = result["comparisons"][0]
    assert comparison["status"] == "value_conflict"
    assert all(row["total"]["evidence_ref"]["page_number"] == 1 for row in comparison["documents"])


def test_cross_document_correlation_does_not_link_by_amount_or_date_alone():
    documents = [{
        "document_id": f"doc-{index}", "filename": f"record-{index}.png", "processing_status": "ocr_completed",
        "blocks": [{"block_id": f"b-{index}", "page_index": 0, "text": f"Invoice No: REF-{index}001\nTotal: 100.00\nDate: 2024-04-01"}],
    } for index in (1, 2)]
    result = build_cross_document_correlation(documents)
    assert result["links"] == []
    assert result["comparisons"] == []
    assert result["summary"]["contradictions"] == 0


def test_cross_document_correlation_keeps_identifier_types_separate():
    docs = [
        {"document_id": "invoice", "filename": "invoice.png", "blocks": [{"block_id": "i", "text": "Invoice No: SAME-1001"}]},
        {"document_id": "reference", "filename": "reference.png", "blocks": [{"block_id": "r", "text": "Reference No: SAME-1001"}]},
    ]
    result = build_cross_document_correlation(docs)
    assert result["links"] == []


def test_cross_document_correlation_empty_ocr_produces_no_unsupported_links():
    result = build_cross_document_correlation([{
        "document_id": "empty", "filename": "empty.png", "processing_status": "ocr_completed", "blocks": [], "text": "",
    }])
    assert result["status"] == "no_ocr_results"
    assert result["links"] == []
    assert result["summary"]["candidate_links"] == 0


@pytest.mark.asyncio
async def test_analysis_review_dossier_and_evaluation(client, monkeypatch, tmp_path):
    auth_routes._attempts.clear()
    async def no_provider_call(snapshot):
        return {"status": "unavailable", "text": None, "evidence_refs": [], "reason": "Provider calls are disabled in integration tests."}
    monkeypatch.setattr(investigation_analysis_service, "generate_grounded_narrative", no_provider_call)
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
    assert run.json()["visual_forensics"]["status"] == "not_run"
    assert run.json()["visual_forensics"]["available_signals"] == []
    assert run.json()["visual_forensics"]["checks"] == []
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

    correlation_response = await client.get(f"/api/v1/investigations/{case_id}/correlation")
    assert correlation_response.status_code == 200
    correlation = correlation_response.json()
    assert correlation["summary"]["documents_with_ocr"] == 2
    assert correlation["summary"]["candidate_links"] == 1
    assert correlation["summary"]["contradictions"] == 1
    assert {ref["document_id"] for ref in correlation["links"][0]["evidence_refs"]} == set(texts)
    candidate_id = correlation["links"][0]["id"]
    reviewed = await client.patch(
        f"/api/v1/investigations/{case_id}/correlation/{candidate_id}/review",
        headers=headers,
        json={"status": "confirmed", "reason": "Verified the cited source fields."},
    )
    assert reviewed.status_code == 200
    assert reviewed.json()["review_status"] == "confirmed"
    rerun = await client.post(f"/api/v1/investigations/{case_id}/correlation-runs", headers=headers)
    assert rerun.status_code == 201
    assert len(rerun.json()["links"]) == 1
    after_rerun = (await client.get(f"/api/v1/investigations/{case_id}/correlation")).json()
    assert after_rerun["summary"]["confirmed_links"] == 1
    assert after_rerun["summary"]["unreviewed_links"] == 0
    assert after_rerun["links"][0]["review_reason"] == "Verified the cited source fields."

    dossier = await client.post(f"/api/v1/investigations/{case_id}/dossiers", headers=headers)
    assert dossier.status_code == 201
    assert "INV-10001" in dossier.json()["html"]
    assert dossier.json()["snapshot"]["cross_document_links"][0]["review_status"] == "confirmed"
    assert "invoice-a.png" in dossier.json()["html"]
    assert len(dossier.json()["snapshot"]["cross_document_discrepancies"]) == 1
    assert dossier.json()["snapshot"]["extracted_information"]
    assert dossier.json()["snapshot"]["audit_activity"]
    assert any(item["action"] == "dossier.created" for item in dossier.json()["snapshot"]["audit_activity"])
    assert dossier.json()["snapshot"]["findings"]
    assert "labeled totals" in dossier.json()["html"]
    assert "No model narrative was stored" in dossier.json()["html"]
    assert dossier.json()["snapshot"]["report"]["version"] == dossier.json()["version"]
    assert dossier.json()["content_sha256"] == dossier.json()["snapshot"]["report"]["integrity_sha256"]
    assert dossier.json()["snapshot"]["audit_chain_verification"]["valid"] is True
    old_dossier_id = dossier.json()["id"]
    newer_dossier = await client.post(f"/api/v1/investigations/{case_id}/dossiers", headers=headers)
    assert newer_dossier.status_code == 201 and newer_dossier.json()["version"] == 2
    exported = await client.get(f"/api/v1/investigations/{case_id}/dossiers/{dossier.json()['id']}/export.json")
    assert exported.status_code == 200
    assert exported.headers["content-disposition"] == f'attachment; filename="dossier-v{dossier.json()["version"]}.json"'
    assert exported.json()["investigation"]["id"] == case_id
    exported_html = await client.get(f"/api/v1/investigations/{case_id}/dossiers/{dossier.json()['id']}/export.html")
    assert exported_html.status_code == 200
    assert exported_html.headers["content-type"].startswith("text/html")
    assert exported_html.headers["content-disposition"] == f'attachment; filename="dossier-v{dossier.json()["version"]}.html"'
    assert "DocForensIQ · Evidence intelligence" in exported_html.text
    assert exported.json()["report"]["integrity_sha256"] == dossier.json()["content_sha256"]
    assert "Cross-document verification" in exported_html.text
    assert "Visual-forensics results" in exported_html.text
    exported_pdf = await client.get(f"/api/v1/investigations/{case_id}/dossiers/{old_dossier_id}/export.pdf")
    assert exported_pdf.status_code == 200
    assert exported_pdf.headers["content-type"] == "application/pdf"
    assert exported_pdf.headers["content-disposition"] == 'attachment; filename="dossier-v1.pdf"'
    assert exported_pdf.content.startswith(b"%PDF-") and exported_pdf.content.rstrip().endswith(b"%%EOF")
    pdf_text = "\n".join(page.extract_text() or "" for page in PdfReader(io.BytesIO(exported_pdf.content)).pages)
    assert "Executive summary" in pdf_text and "Visual-forensics results" in pdf_text
    assert "Cross-document verification" in pdf_text and "INV-10001" in pdf_text
    (tmp_path / "dossier-v1.pdf").write_bytes(exported_pdf.content)
    import shutil
    import subprocess
    pdfinfo = shutil.which("pdfinfo")
    if pdfinfo:
        inspection = subprocess.run([pdfinfo, str(tmp_path / "dossier-v1.pdf")], capture_output=True, text=True, check=True)
        assert "Pages:" in inspection.stdout and "Page size:" in inspection.stdout
    audit = (await client.get(f"/api/v1/investigations/{case_id}/audit")).json()["items"]
    audit_response = await client.get(f"/api/v1/investigations/{case_id}/audit")
    assert audit_response.json()["verification"]["valid"] is True
    assert audit_response.json()["verification"]["verified_count"] == len(audit)
    hashes = {item["event_hash"] for item in audit}
    assert sum(item["previous_hash"] == "genesis" for item in audit) == 1
    assert all(item["previous_hash"] == "genesis" or item["previous_hash"] in hashes for item in audit)
    assert [item["action"] for item in audit].count("evidence.uploaded") == 2
    assert [item["action"] for item in audit].count("dossier.exported") == 3
    export_details = [item["details"] for item in audit if item["action"] == "dossier.exported"]
    assert {item["format"] for item in export_details} == {"json", "html", "pdf"}
    assert all(item["actor_user_id"] and item["investigation_id"] == case_id and item["timestamp"] for item in export_details)
    assert [item["action"] for item in audit].count("investigation.created") == 1
    async with app.state.test_sessions() as session:
        persisted_dossier = await session.scalar(select(DossierSnapshot).where(DossierSnapshot.id == UUID(old_dossier_id)))
        persisted_dossier.html += "<!-- unexpected content mutation -->"
        await session.commit()
    tampered = await client.get(f"/api/v1/investigations/{case_id}/dossiers/{old_dossier_id}/export.pdf")
    assert tampered.status_code == 409


@pytest.mark.asyncio
async def test_empty_investigation_can_generate_and_export_a_dossier(client, monkeypatch):
    auth_routes._attempts.clear()
    monkeypatch.setattr(auth_routes, "settings", replace(settings, self_registration_enabled=True))
    await client.post("/api/v1/auth/register", json={"full_name": "Empty Case Investigator", "email": "empty-dossier@example.com", "password": PASSWORD})
    headers = {"X-CSRF-Token": client.cookies.get("dfi_csrf")}
    case = await client.post("/api/v1/investigations", headers=headers, json={"title": "Empty source review"})
    case_id = case.json()["id"]
    dossier = await client.post(f"/api/v1/investigations/{case_id}/dossiers", headers=headers)
    assert dossier.status_code == 201
    assert dossier.json()["snapshot"]["evidence_inventory"] == []
    assert dossier.json()["snapshot"]["ai_narrative"]["status"] == "skipped"
    assert "No documents were stored" in dossier.json()["html"]
    pdf = await client.get(f"/api/v1/investigations/{case_id}/dossiers/{dossier.json()['id']}/export.pdf")
    assert pdf.status_code == 200 and pdf.content.startswith(b"%PDF-")
    assert (await client.get(f"/api/v1/investigations/{case_id}/audit")).json()["verification"]["valid"] is True

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
    hidden_correlation = await client.get(f"/api/v1/investigations/{case_id}/correlation")
    assert hidden_correlation.status_code == 404

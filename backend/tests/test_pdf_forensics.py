import hashlib
import io
from dataclasses import replace
from datetime import datetime, timedelta, timezone

import fitz
import pytest

from app.api.v1 import analysis as analysis_routes, auth as auth_routes, investigations as investigation_routes
from app.core.config import settings
from app.services.pdf_forensics import analyze_pdf
from app.services.pdf_forensics import compare_pdf_native_text_to_ocr, verify_pdf_signatures
from app.services.evidence_correlation import correlate_pdf_text_and_visual


PASSWORD = "StrongPassword123"


def _hash(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_pdf_structure_reports_provenance_and_text_geometry(tmp_path):
    source = tmp_path / "clean.pdf"
    doc = fitz.open()
    page = doc.new_page(width=612, height=792)
    page.insert_text((72, 90), "Invoice total: 125.00", fontsize=12)
    page.add_text_annot((72, 120), "review marker")
    image = fitz.Pixmap(fitz.csRGB, fitz.IRect(0, 0, 8, 8), False)
    image.clear_with(0x88)
    page.insert_image(fitz.Rect(200, 200, 216, 216), stream=image.tobytes("png"))
    doc.set_metadata({"title": "Synthetic clean document", "creator": "DocForensIQ fixture"})
    doc.save(source)
    doc.close()

    result = analyze_pdf(source, _hash(source))

    assert result["status"] == "completed"
    assert result["source_sha256"] == _hash(source)
    assert result["document"]["page_count"] == 1
    assert result["document"]["header_version"].startswith("1.")
    assert result["document"]["metadata"]["title"] == "Synthetic clean document"
    assert result["pages"][0]["width_points"] == 612
    assert any("Invoice total" in (span["text"] or "") for span in result["pages"][0]["text_spans"])
    assert result["pages"][0]["text_spans"][0]["bbox"]["x0"] > 0
    assert result["pages"][0]["annotations"]
    assert result["pages"][0]["images"][0]["sha256"]
    assert any("revision" in limitation.lower() for limitation in result["limitations"])


def test_pdf_structure_reports_incremental_revision_without_classifying_it(tmp_path):
    source = tmp_path / "incremental.pdf"
    doc = fitz.open()
    doc.new_page()
    doc.save(source)
    doc.close()
    doc = fitz.open(source)
    doc.set_metadata({"title": "Legitimate incremental save"})
    doc.save(source, incremental=True, encryption=fitz.PDF_ENCRYPT_KEEP)
    doc.close()

    result = analyze_pdf(source, _hash(source))

    assert result["document"]["revision_count"] >= 2
    assert result["revision_inventory"]["status"] == "completed"
    assert len(result["revision_inventory"]["revisions"]) == 2
    assert {"object_number", "generation", "state", "byte_offset"} <= set(result["revision_inventory"]["revisions"][0]["object_inventory"][0])
    assert result["revision_inventory"]["revisions"][1]["changes"]["added"]
    assert result["status"] == "completed"
    assert not any("malicious" in str(value).lower() for value in result["limitations"])


def test_native_text_ocr_comparison_requires_validated_render_geometry():
    report = {"source_sha256": "a" * 64, "pages": [{"page_number": 1, "width_points": 100, "height_points": 200, "text_spans": [{"text": "TOTAL 100", "bbox": {"x0": 10, "y0": 20, "x1": 50, "y1": 32}}]}]}
    geometry = {"mapping_version": "ocr_pixel_to_pdf_v1", "render_width_px": 200, "render_height_px": 400, "page_width_points": 100, "page_height_points": 200, "origin_x_points": 0, "origin_y_points": 0}
    blocks = [{"block_id": "ocr-1", "page_index": 0, "text": "TOTAL 900", "source_path": "raw.page[0]", "bbox_space": "rendered_pixels_top_left", "page_geometry": geometry, "engine_bbox": [20, 40, 100, 64]}]
    result = compare_pdf_native_text_to_ocr(report, [{"page_number": 1, "width": 200, "height": 400}], blocks)
    assert result["status"] == "completed"
    assert len(result["discrepancies"]) == 1
    assert result["discrepancies"][0]["ocr_source_ref"]["bbox_pdf_points"] == {"x0": 10.0, "y0": 20.0, "x1": 50.0, "y1": 32.0}
    invalid = compare_pdf_native_text_to_ocr(report, [{"page_number": 1, "width": 100, "height": 200}], blocks)
    assert invalid["discrepancies"] == []
    assert invalid["skipped_pages"][0]["reason"] == "render_geometry_does_not_match_pdf_page"


def test_cross_method_links_require_same_document_page_and_bbox_overlap():
    pdf_runs = [{"id": "pdf-run", "document_id": "doc-1", "source_sha256": "a" * 64, "result": {"text_comparison": {"discrepancies": [{"page_number": 1, "native_source_ref": {"bbox_pdf_points": {"x0": 10, "y0": 10, "x1": 30, "y1": 30}}, "ocr_source_ref": {"block_id": "block-1"}}]}}}]
    visual_runs = [{"id": "visual-run", "document_id": "doc-1", "result": {"observations": [{"page_number": 1, "check_type": "copy_move", "evidence_ref": {"page_number": 1}, "regions": [{"x0": 15, "y0": 15, "x1": 25, "y1": 25}]}]}}]
    linked = correlate_pdf_text_and_visual(pdf_runs, visual_runs)
    assert len(linked["links"]) == 1
    assert linked["links"][0]["source_sha256"] == "a" * 64
    assert {item["method"] for item in linked["links"][0]["source_references"]} == {"native_pdf_text", "ocr", "visual"}
    visual_runs[0]["document_id"] = "other-doc"
    assert correlate_pdf_text_and_visual(pdf_runs, visual_runs)["links"] == []


def test_pyhanko_signature_verification_separates_integrity_and_trust(tmp_path):
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.x509.oid import NameOID
    from pyhanko.pdf_utils.incremental_writer import IncrementalPdfFileWriter
    from pyhanko.sign import signers
    from pyhanko.sign.signers.pdf_signer import PdfSignatureMetadata

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "Synthetic DocForensIQ Fixture")])
    certificate = (x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(key.public_key())
        .serial_number(x509.random_serial_number()).not_valid_before(datetime.now(timezone.utc) - timedelta(minutes=1))
        .not_valid_after(datetime.now(timezone.utc) + timedelta(days=1))
        .add_extension(x509.KeyUsage(digital_signature=True, content_commitment=True, key_encipherment=False, data_encipherment=False, key_agreement=False, key_cert_sign=False, crl_sign=False, encipher_only=None, decipher_only=None), critical=True)
        .sign(key, hashes.SHA256()))
    key_path, cert_path = tmp_path / "fixture-key.pem", tmp_path / "fixture-cert.pem"
    key_path.write_bytes(key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()))
    cert_path.write_bytes(certificate.public_bytes(serialization.Encoding.PEM))
    signer = signers.SimpleSigner.load(str(key_path), str(cert_path))
    doc = fitz.open()
    doc.new_page().insert_text((72, 72), "Signed fixture")
    source_bytes = doc.tobytes()
    doc.close()
    writer = IncrementalPdfFileWriter(io.BytesIO(source_bytes))
    signed_stream = io.BytesIO()
    signers.PdfSigner(PdfSignatureMetadata(field_name="SyntheticSignature"), signer).sign_pdf(writer, output=signed_stream)
    signed_path = tmp_path / "signed.pdf"
    signed_path.write_bytes(signed_stream.getvalue())

    result = verify_pdf_signatures(signed_path)

    assert result["library"] == "pyHanko"
    assert result["status"] == "completed"
    assert result["signatures"][0]["cryptographic_integrity"] == "valid"
    assert result["signatures"][0]["certificate_validation"] != "trusted"


def test_pdf_structure_rejects_wrong_source_hash(tmp_path):
    source = tmp_path / "source.pdf"
    doc = fitz.open()
    doc.new_page()
    doc.save(source)
    doc.close()

    try:
        analyze_pdf(source, "0" * 64)
    except ValueError as exc:
        assert "SHA-256" in str(exc)
    else:
        raise AssertionError("a mismatched evidence hash must be rejected")


def test_encrypted_pdf_returns_explicit_unavailable_state(tmp_path):
    source = tmp_path / "encrypted.pdf"
    doc = fitz.open()
    doc.new_page()
    doc.save(source, encryption=fitz.PDF_ENCRYPT_AES_256, user_pw="secret", owner_pw="owner")
    doc.close()

    result = analyze_pdf(source, _hash(source))

    assert result["status"] == "unavailable"
    assert result["document"]["encrypted"] is True
    assert result["pages"] == []
    assert any("encrypted" in limitation.lower() for limitation in result["limitations"])


@pytest.mark.asyncio
async def test_pdf_structure_api_persists_scoped_report_and_summary(client, monkeypatch, tmp_path):
    monkeypatch.setattr(auth_routes, "settings", replace(settings, self_registration_enabled=True))
    monkeypatch.setattr(investigation_routes, "settings", replace(settings, document_storage_path=str(tmp_path)))
    monkeypatch.setattr(analysis_routes, "settings", replace(settings, document_storage_path=str(tmp_path)))
    registration = await client.post("/api/v1/auth/register", json={
        "full_name": "PDF Analyst", "email": "pdf-analyst@example.com", "password": PASSWORD,
    })
    assert registration.status_code == 201
    headers = {"X-CSRF-Token": client.cookies.get("dfi_csrf")}
    investigation = await client.post("/api/v1/investigations", headers=headers, json={"title": "PDF structure test"})
    assert investigation.status_code == 201

    pdf = fitz.open()
    page = pdf.new_page()
    page.insert_text((72, 72), "Controlled PDF fixture")
    image = fitz.Pixmap(fitz.csRGB, fitz.IRect(0, 0, 8, 8), False)
    image.clear_with(0x44)
    page.insert_image(fitz.Rect(150, 150, 166, 166), stream=image.tobytes("png"))
    source = pdf.tobytes()
    pdf.close()
    uploaded = await client.post(
        f"/api/v1/investigations/{investigation.json()['id']}/documents",
        headers={**headers, "X-File-Name": "fixture.pdf", "Content-Type": "application/pdf"},
        content=source,
    )
    assert uploaded.status_code == 201
    document_id = uploaded.json()["id"]
    analysis = await client.post(
        f"/api/v1/investigations/{investigation.json()['id']}/documents/{document_id}/pdf-forensics",
        headers=headers,
    )
    assert analysis.status_code == 201, analysis.text
    assert analysis.json()["source_sha256"] == uploaded.json()["sha256"]
    assert analysis.json()["result"]["pages"][0]["text_spans"][0]["text"] == "Controlled PDF fixture"
    assert analysis.json()["result"]["text_comparison"]["status"] == "unavailable"
    assert analysis.json()["result"]["signature_verification"]["status"] == "no_signatures"
    assert analysis.json()["result"]["revision_inventory"]["status"] == "completed"
    image_artifact_id = analysis.json()["result"]["pages"][0]["images"][0]["artifact_id"]
    artifact = await client.get(f"/api/v1/investigations/{investigation.json()['id']}/pdf-forensic-artifacts/{image_artifact_id}")
    assert artifact.status_code == 200
    assert artifact.headers["x-content-sha256"] == analysis.json()["result"]["pages"][0]["images"][0]["sha256"]
    assert artifact.content.startswith(b"\x89PNG\r\n\x1a\n")

    latest = await client.get(f"/api/v1/investigations/{investigation.json()['id']}/documents/{document_id}/pdf-forensics/latest")
    assert latest.json()["item"]["id"] == analysis.json()["id"]
    summary = await client.get(f"/api/v1/investigations/{investigation.json()['id']}/pdf-forensics/summary")
    assert summary.json()["summary"] == {"pdf_documents": 1, "documents_analyzed": 1, "pages_analyzed": 1, "completed": 1, "partial": 0}
    correlations = await client.get(f"/api/v1/investigations/{investigation.json()['id']}/cross-method-correlations")
    assert correlations.status_code == 200
    assert correlations.json()["links"] == []

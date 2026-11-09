from dataclasses import replace
import hashlib
from pathlib import Path

import pytest

from app.api.v1 import analysis as analysis_routes, auth as auth_routes, investigations as investigation_routes
from app.core.config import settings
from app.main import app
from app.services.ocr_worker import _blocks
from app.services import ocr_worker, relationship_extraction
from app.services.relationship_extraction import EntityMention
from pydantic import ValidationError
from scripts import paddle_service


PASSWORD = "StrongPassword123"


@pytest.mark.asyncio
async def test_image_upload_is_persisted_and_scoped_to_owned_investigation(client, monkeypatch, tmp_path):
    monkeypatch.setattr(auth_routes, "settings", replace(settings, self_registration_enabled=True))
    monkeypatch.setattr(investigation_routes, "settings", replace(settings, document_storage_path=str(tmp_path)))
    registration = await client.post("/api/v1/auth/register", json={
        "full_name": "Test Investigator", "email": "upload@example.com", "password": PASSWORD,
    })
    assert registration.status_code == 201
    case = await client.post("/api/v1/investigations", headers={"X-CSRF-Token": client.cookies.get("dfi_csrf")}, json={"title": "Image intake"})
    assert case.status_code == 201
    image = b"\x89PNG\r\n\x1a\n" + b"image-content"
    response = await client.post(
        f"/api/v1/investigations/{case.json()['id']}/documents",
        headers={"X-CSRF-Token": client.cookies.get("dfi_csrf"), "X-File-Name": "../../receipt.png", "X-Source-Description": "Received from records department", "Content-Type": "image/png"},
        content=image,
    )
    assert response.status_code == 201
    result = response.json()
    assert result["filename"] == "receipt.png"
    assert result["processing_status"] == "queued"
    assert result["sha256"] == hashlib.sha256(image).hexdigest()
    assert result["source_description"] == "Received from records department"
    assert result["uploaded_by_user_id"]
    stored = list(tmp_path.iterdir())
    assert len(stored) == 1 and stored[0].name != result["filename"]
    assert stored[0].read_bytes() == image
    assert (await client.get(f"/api/v1/investigations/{case.json()['id']}/documents/{result['id']}/status")).json()["status"] == "queued"
    listed = (await client.get(f"/api/v1/investigations/{case.json()['id']}/documents")).json()
    assert listed[0]["sha256"] == result["sha256"]
    details = await client.get(f"/api/v1/investigations/{case.json()['id']}/documents/{result['id']}")
    assert details.status_code == 200
    assert details.json()["source_description"] == "Received from records department"
    source = await client.get(f"/api/v1/investigations/{case.json()['id']}/documents/{result['id']}/file")
    assert source.status_code == 200
    assert source.content == image
    assert source.headers["x-content-sha256"] == result["sha256"]


@pytest.mark.asyncio
async def test_pdf_upload_requires_pdf_signature_and_end_marker(client, monkeypatch, tmp_path):
    monkeypatch.setattr(auth_routes, "settings", replace(settings, self_registration_enabled=True))
    monkeypatch.setattr(investigation_routes, "settings", replace(settings, document_storage_path=str(tmp_path)))
    await client.post("/api/v1/auth/register", json={"full_name": "Test", "email": "pdf@example.com", "password": PASSWORD})
    case = await client.post("/api/v1/investigations", headers={"X-CSRF-Token": client.cookies.get("dfi_csrf")}, json={"title": "PDF intake"})
    case_id = case.json()["id"]
    headers = {"X-CSRF-Token": client.cookies.get("dfi_csrf"), "X-File-Name": "evidence.pdf", "Content-Type": "application/pdf"}
    invalid = await client.post(f"/api/v1/investigations/{case_id}/documents", headers=headers, content=b"%PDF-1.7\nincomplete")
    assert invalid.status_code == 415
    assert list(tmp_path.iterdir()) == []

    pdf = b"%PDF-1.7\nsample document\n%%EOF\n"
    valid = await client.post(f"/api/v1/investigations/{case_id}/documents", headers=headers, content=pdf)
    assert valid.status_code == 201
    assert valid.json()["sha256"] == hashlib.sha256(pdf).hexdigest()
    assert valid.json()["content_type"] == "application/pdf"


@pytest.mark.asyncio
async def test_integrity_endpoint_verifies_stored_bytes(client, monkeypatch, tmp_path):
    auth_routes._attempts.clear()
    monkeypatch.setattr(auth_routes, "settings", replace(settings, self_registration_enabled=True))
    monkeypatch.setattr(investigation_routes, "settings", replace(settings, document_storage_path=str(tmp_path)))
    monkeypatch.setattr(analysis_routes, "settings", replace(settings, document_storage_path=str(tmp_path)))
    await client.post("/api/v1/auth/register", json={"full_name": "Test", "email": "integrity@example.com", "password": PASSWORD})
    headers = {"X-CSRF-Token": client.cookies.get("dfi_csrf")}
    case = await client.post("/api/v1/investigations", headers=headers, json={"title": "Integrity check"})
    content = b"\x89PNG\r\n\x1a\noriginal"
    uploaded = await client.post(
        f"/api/v1/investigations/{case.json()['id']}/documents",
        headers={**headers, "X-File-Name": "evidence.png", "Content-Type": "image/png"},
        content=content,
    )
    document = uploaded.json()
    integrity_url = f"/api/v1/investigations/{case.json()['id']}/documents/{document['id']}/integrity"
    verified = await client.get(integrity_url)
    assert verified.status_code == 200
    assert verified.json()["status"] == "verified"
    next(tmp_path.iterdir()).write_bytes(content + b"changed")
    mismatch = await client.get(integrity_url)
    assert mismatch.status_code == 200
    assert mismatch.json()["status"] == "mismatch"
    auth_routes._attempts.clear()


@pytest.mark.asyncio
async def test_worker_persists_immutable_extraction_run_pages_and_spans(client, monkeypatch, tmp_path):
    auth_routes._attempts.clear()
    monkeypatch.setattr(auth_routes, "settings", replace(settings, self_registration_enabled=True))
    monkeypatch.setattr(investigation_routes, "settings", replace(settings, document_storage_path=str(tmp_path)))
    monkeypatch.setattr(ocr_worker, "settings", replace(settings, document_storage_path=str(tmp_path)))
    monkeypatch.setattr(ocr_worker, "SessionLocal", app.state.test_sessions)
    monkeypatch.setattr(relationship_extraction, "extract", lambda _blocks: ({"schema_version": "relationship_extraction_v1", "entities": [], "relationships": []}, {"usage": []}))
    monkeypatch.setattr(
        ocr_worker,
        "_run_engine",
        lambda _path: ({"engine_output": [{"page_index": 0, "parsing_res_list": [
            {"block_id": "original-block-1", "text": "Total 250.00", "bbox": [3, 5, 120, 25], "confidence": 0.94},
        ]}], "page_metadata": [{"page_number": 1, "width": 640, "height": 480}]}, "test-engine-1"),
    )
    await client.post("/api/v1/auth/register", json={"full_name": "Worker Test", "email": "worker@example.com", "password": PASSWORD})
    headers = {"X-CSRF-Token": client.cookies.get("dfi_csrf")}
    case = await client.post("/api/v1/investigations", headers=headers, json={"title": "OCR run"})
    upload = await client.post(
        f"/api/v1/investigations/{case.json()['id']}/documents",
        headers={**headers, "X-File-Name": "receipt.png", "Content-Type": "image/png"},
        content=b"\x89PNG\r\n\x1a\nsource",
    )
    assert upload.status_code == 201
    assert await ocr_worker.process_one()
    runs_url = f"/api/v1/investigations/{case.json()['id']}/documents/{upload.json()['id']}/extraction-runs"
    runs = await client.get(runs_url)
    assert runs.status_code == 200
    assert len(runs.json()["items"]) == 1
    run = runs.json()["items"][0]
    assert run["status"] == "completed"
    assert run["provider_version"] == "test-engine-1"
    detail = await client.get(f"{runs_url}/{run['id']}")
    assert detail.status_code == 200
    assert detail.json()["pages"][0]["page_number"] == 1
    assert detail.json()["pages"][0]["width"] == 640
    assert detail.json()["pages"][0]["height"] == 480
    assert detail.json()["pages"][0]["text"] == "Total 250.00"
    span = detail.json()["spans"][0]
    assert span["block_id"] == "block_000001"
    assert span["bounding_box"] == [3, 5, 120, 25]
    assert span["confidence"] == 0.94
    audit_actions = [item["action"] for item in (await client.get(f"/api/v1/investigations/{case.json()['id']}/audit")).json()["items"]]
    assert audit_actions[-2:] == ["extraction.completed", "relationship_extraction.completed"]
    auth_routes._attempts.clear()


@pytest.mark.asyncio
async def test_upload_rejects_mismatched_content_and_cleans_partial_file(client, monkeypatch, tmp_path):
    monkeypatch.setattr(auth_routes, "settings", replace(settings, self_registration_enabled=True))
    monkeypatch.setattr(investigation_routes, "settings", replace(settings, document_storage_path=str(tmp_path)))
    await client.post("/api/v1/auth/register", json={"full_name": "Test", "email": "mime@example.com", "password": PASSWORD})
    case = await client.post("/api/v1/investigations", headers={"X-CSRF-Token": client.cookies.get("dfi_csrf")}, json={"title": "Image intake"})
    response = await client.post(
        f"/api/v1/investigations/{case.json()['id']}/documents",
        headers={"X-CSRF-Token": client.cookies.get("dfi_csrf"), "X-File-Name": "bad.png", "Content-Type": "image/png"},
        content=b"not a png",
    )
    assert response.status_code == 415
    assert list(tmp_path.iterdir()) == []


def test_ocr_block_adapter_keeps_source_paths_and_engine_fields():
    raw = [{"page_index": 2, "parsing_res_list": [{"block_id": "blk-7", "text": "ACME Ltd", "bbox": [1, 2, 3, 4], "confidence": 0.91}]}]
    blocks = _blocks(raw)
    assert blocks == [{"block_id": "block_000001", "source_path": "root[0].parsing_res_list[0]", "text": "ACME Ltd", "page_index": 2, "engine_block_id": "blk-7", "engine_bbox": [1, 2, 3, 4], "engine_confidence": 0.91}]


def test_ocr_block_adapter_reads_ppstructure_payload():
    raw = [{"res": {"parsing_res_list": [
        {"block_label": "doc_title", "block_content": "ACME Ltd", "block_bbox": [1, 2, 3, 4], "block_id": 0, "block_order": 1},
        {"block_label": "image", "block_content": "", "block_bbox": [0, 0, 1, 1], "block_id": 1, "block_order": None},
    ]}}]
    blocks = _blocks(raw)
    assert blocks == [{"block_id": "block_000001", "source_path": "root[0].res.parsing_res_list[0]", "text": "ACME Ltd", "engine_block_id": 0, "engine_bbox": [1, 2, 3, 4], "engine_label": "doc_title"}]


def test_ocr_block_adapter_falls_back_to_recognized_lines():
    raw = [{"res": {"overall_ocr_res": {
        "rec_texts": ["Line one", "Line two"], "rec_boxes": [[1, 2, 3, 4], [5, 6, 7, 8]], "rec_scores": [0.9, 0.8],
    }}}]
    blocks = _blocks(raw)
    assert [block["text"] for block in blocks] == ["Line one", "Line two"]
    assert blocks[0]["engine_confidence"] == 0.9
    assert blocks[1]["engine_bbox"] == [5, 6, 7, 8]


def test_ocr_input_path_adds_suffix_for_bare_storage_key(tmp_path):
    stored = tmp_path / "stored"
    stored.write_bytes(b"\x89PNG\r\n\x1a\n" + b"payload")
    effective, temporary = paddle_service._input_path(str(stored))
    try:
        assert Path(effective).suffix == ".png"
        assert Path(effective).read_bytes() == stored.read_bytes()
    finally:
        Path(effective).unlink()


def test_ocr_input_path_rejects_unknown_format(tmp_path):
    stored = tmp_path / "stored"
    stored.write_bytes(b"not a document")
    with pytest.raises(ValueError):
        paddle_service._input_path(str(stored))


def test_ocr_input_path_supports_big_endian_tiff(tmp_path):
    stored = tmp_path / "stored"
    stored.write_bytes(b"MM\x00*" + b"payload")
    effective, temporary = paddle_service._input_path(str(stored))
    try:
        assert Path(effective).suffix == ".tiff"
        assert Path(effective).read_bytes() == stored.read_bytes()
    finally:
        if temporary:
            Path(temporary).unlink()


def test_entity_schema_rejects_unsupported_types_and_extra_fields():
    base = {"mention_id": "m1", "mention_text": "Name", "source_block_ids": ["block_000001"], "confidence": None}
    with pytest.raises(ValidationError):
        EntityMention(**base, entity_type="FRAUD")
    with pytest.raises(ValidationError):
        EntityMention(**base, entity_type="PERSON", unsupported="value")

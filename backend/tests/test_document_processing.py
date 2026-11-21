from dataclasses import replace
import hashlib
import io
import json
import uuid
from datetime import datetime, timezone
from pathlib import Path
import shutil

import pytest

from app.api.v1 import analysis as analysis_routes, auth as auth_routes, investigations as investigation_routes
from app.core.config import settings
from app.main import app
from app.services.ocr_worker import _blocks
from app.services import audit_chain, ocr_worker, relationship_extraction
from app.services.llm_client import LLMHTTPError
from app.services.relationship_extraction import EntityMention
from pydantic import ValidationError
from scripts import paddle_service
import fitz


@pytest.mark.asyncio
async def test_audit_append_order_remains_stable_when_clock_values_tie(client, monkeypatch):
    from sqlalchemy import select

    from app.models.entities import AuditChainEvent

    monkeypatch.setattr(auth_routes, "settings", replace(settings, self_registration_enabled=True))
    await client.post("/api/v1/auth/register", json={
        "full_name": "Audit Timing Test", "email": "audit-timing@example.com", "password": "StrongPassword123",
    })
    case = await client.post(
        "/api/v1/investigations",
        headers={"X-CSRF-Token": client.cookies.get("dfi_csrf")},
        json={"title": "Audit timestamp ordering"},
    )
    investigation_id = uuid.UUID(case.json()["id"])
    fixed_time = datetime(2099, 1, 1, tzinfo=timezone.utc)

    class FixedClock:
        @staticmethod
        def now(_tz=None):
            return fixed_time

    monkeypatch.setattr(audit_chain, "datetime", FixedClock)
    async with app.state.test_sessions() as db:
        await audit_chain.append_audit(
            db, investigation_id=investigation_id, actor_user_id=None,
            action="test.first", target_type="test", target_id="one",
        )
        await db.flush()
        await audit_chain.append_audit(
            db, investigation_id=investigation_id, actor_user_id=None,
            action="test.second", target_type="test", target_id="two",
        )
        await db.commit()
        rows = (await db.scalars(
            select(AuditChainEvent)
            .where(AuditChainEvent.investigation_id == investigation_id)
            .order_by(AuditChainEvent.created_at.asc(), AuditChainEvent.id.asc())
        )).all()

    actions = [row.action for row in rows]
    assert actions[-2:] == ["test.first", "test.second"]
    assert rows[-2].created_at < rows[-1].created_at
    from app.services.audit_chain import verify_audit_chain
    assert verify_audit_chain(rows)["valid"] is True


def test_relationship_extraction_requires_configured_groq_key(monkeypatch):
    monkeypatch.setattr(relationship_extraction, "settings", replace(
        settings, llm_api_key="",
    ))

    with pytest.raises(relationship_extraction.RelationshipExtractionError, match="provider API credential is not configured"):
        relationship_extraction.extract([{"block_id": "synthetic-b1", "text": "Synthetic test text"}])


def test_relationship_extraction_sanitizes_groq_auth_failure(monkeypatch):
    monkeypatch.setattr(relationship_extraction, "settings", replace(
        settings,
        llm_api_key="synthetic-test-token",
        llm_base_url="https://api.groq.com/openai/v1",
        llm_model="openai/gpt-oss-120b",
    ))

    def unauthorized(_messages, **_kwargs):
        raise LLMHTTPError(401)

    monkeypatch.setattr(relationship_extraction, "chat_completion", unauthorized)
    with pytest.raises(relationship_extraction.RelationshipExtractionError, match="authentication failed; verify") as exc:
        relationship_extraction.extract([{"block_id": "synthetic-b1", "text": "Synthetic test text"}])
    assert exc.value.code == "provider_authentication_error"
    assert exc.value.retryable is False
    assert "synthetic-test-token" not in str(exc.value)


PASSWORD = "StrongPassword123"


def test_pdf_ocr_rasterization_persists_measured_pixel_to_page_mapping(tmp_path):
    source = tmp_path / "source.pdf"
    document = fitz.open()
    page = document.new_page(width=612, height=792)
    page.insert_text((72, 72), "Coordinates")
    document.save(source)
    document.close()

    raster_dir, temporary_dir, pages = paddle_service._render_pdf_pages(str(source), dpi=144)

    assert raster_dir == temporary_dir
    assert len(pages) == 1
    assert pages[0]["bbox_space"] == "rendered_pixels_top_left"
    assert pages[0]["geometry"]["render_width_px"] == pages[0]["width"]
    assert pages[0]["geometry"]["render_height_px"] == pages[0]["height"]
    assert pages[0]["geometry"]["page_width_points"] == 612
    assert pages[0]["geometry"]["page_height_points"] == 792
    shutil.rmtree(temporary_dir)


def test_pdf_ocr_rasterization_rejects_empty_documents(tmp_path):
    source = tmp_path / "empty.pdf"
    from pypdf import PdfWriter
    PdfWriter().write(source)

    with pytest.raises(ValueError, match="PDF contains no pages"):
        paddle_service._render_pdf_pages(str(source))


def test_ocr_temporary_cleanup_removes_render_directories_and_files(tmp_path):
    directory = tmp_path / "rendered-pages"
    directory.mkdir()
    (directory / "page-0001.png").write_bytes(b"synthetic")
    temporary_file = tmp_path / "input.png"
    temporary_file.write_bytes(b"synthetic")

    paddle_service._cleanup_temporary(str(directory))
    paddle_service._cleanup_temporary(str(temporary_file))

    assert not directory.exists()
    assert not temporary_file.exists()


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


@pytest.mark.asyncio
async def test_relationship_only_retry_reuses_persisted_ocr(client, monkeypatch, tmp_path):
    from sqlalchemy import select, func

    from app.models.entities import Document, ExtractionRun, OCRResult

    auth_routes._attempts.clear()


@pytest.mark.asyncio
async def test_partial_relationship_chunks_persist_and_retry_only_missing_chunk(client, monkeypatch, tmp_path):
    from sqlalchemy import select, func

    from app.models.entities import Document, ExtractionRun, OCRResult, RelationshipAnalysis

    auth_routes._attempts.clear()
    monkeypatch.setattr(auth_routes, "settings", replace(settings, self_registration_enabled=True))
    monkeypatch.setattr(investigation_routes, "settings", replace(settings, document_storage_path=str(tmp_path)))
    monkeypatch.setattr(ocr_worker, "settings", replace(
        settings, document_storage_path=str(tmp_path), relationship_extraction_enabled=True,
        llm_model="synthetic-model", llm_relationship_chunk_characters=1000,
        llm_relationship_chunk_overlap=0,
    ))
    monkeypatch.setattr(ocr_worker, "SessionLocal", app.state.test_sessions)
    monkeypatch.setattr(relationship_extraction, "settings", replace(
        settings, llm_api_key="synthetic-token", llm_model="synthetic-model",
        llm_relationship_chunk_characters=1000, llm_relationship_chunk_overlap=0,
    ))
    monkeypatch.setattr(
        ocr_worker, "_run_engine",
        lambda _path: ({"page_metadata": [], "engine_output": [
                {"page_index": 0, "parsing_res_list": [{"text": "Entity Alpha Account Alpha " + "a" * 680, "block_id": "engine-a"}]},
                {"page_index": 1, "parsing_res_list": [{"text": "Entity Beta Account Beta " + "b" * 680, "block_id": "engine-b"}]},
        ]}, "test-engine"),
    )
    provider_calls = {"block_000001": 0, "block_000002": 0}
    fail_second_chunk = {"value": True}

    def provider(messages, **_kwargs):
        supplied = json.loads(messages[1]["content"])
        block = supplied[0]
        block_id = block["block_id"]
        provider_calls[block_id] += 1
        if block_id == "block_000002" and fail_second_chunk["value"]:
            raise relationship_extraction.LLMHTTPError(503)
        suffix = "Alpha" if block_id == "block_000001" else "Beta"
        return {
            "content": json.dumps({
                "schema_version": "relationship_extraction_v1",
                "entities": [
                    {"mention_id": "m1", "entity_type": "PERSON", "mention_text": f"Entity {suffix}", "source_block_ids": [block_id], "confidence": 0.9},
                    {"mention_id": "m2", "entity_type": "ACCOUNT", "mention_text": f"Account {suffix}", "source_block_ids": [block_id], "confidence": 0.8},
                ],
                "relationships": [{"relationship_id": "r1", "subject_mention_id": "m1", "predicate": "ASSOCIATED_WITH",
                                   "object_mention_id": "m2", "relationship_kind": "explicit",
                                   "source_block_ids": [block_id], "confidence": 0.85}],
            }),
            "usage": {"total_tokens": 8},
        }

    monkeypatch.setattr(relationship_extraction, "chat_completion", provider)
    await client.post("/api/v1/auth/register", json={"full_name": "Chunk Retry", "email": "chunk-retry@example.com", "password": PASSWORD})
    headers = {"X-CSRF-Token": client.cookies.get("dfi_csrf")}
    case = await client.post("/api/v1/investigations", headers=headers, json={"title": "Partial relationship run"})
    uploaded = await client.post(
        f"/api/v1/investigations/{case.json()['id']}/documents",
        headers={**headers, "X-File-Name": "multi-page.png", "Content-Type": "image/png"},
        content=b"\x89PNG\r\n\x1a\nsource",
    )
    document_id = uuid.UUID(uploaded.json()["id"])
    assert await ocr_worker.process_one()

    async with app.state.test_sessions() as db:
        doc = await db.scalar(select(Document).where(Document.id == document_id))
        ocr = await db.scalar(select(OCRResult).where(OCRResult.document_id == document_id))
        partial = await db.scalar(select(RelationshipAnalysis).where(RelationshipAnalysis.document_id == document_id))
        run_count = await db.scalar(select(func.count(ExtractionRun.id)).where(ExtractionRun.document_id == document_id))
        assert doc.processing_status == "relationship_extraction_failed"
        assert doc.processing_error_code == "provider_temporarily_unavailable"
        assert ocr.recognized_text
        assert len(partial.payload["chunks"]) == 1
        assert len(partial.payload["entities"]) == 2
        assert partial.payload["entities"][0]["source_references"][0]["block_id"] == "block_000001"
        assert len(partial.payload["relationships"]) == 1
        assert partial.payload["relationships"][0]["source_references"]
        assert run_count == 1

    assert provider_calls == {"block_000001": 1, "block_000002": 3}
    retry = await client.post(
        f"/api/v1/investigations/{case.json()['id']}/documents/{document_id}/retry", headers=headers,
    )
    assert retry.status_code == 202
    fail_second_chunk["value"] = False
    assert await ocr_worker.process_one()

    async with app.state.test_sessions() as db:
        doc = await db.scalar(select(Document).where(Document.id == document_id))
        ocr = await db.scalar(select(OCRResult).where(OCRResult.document_id == document_id))
        final = await db.scalar(select(RelationshipAnalysis).where(RelationshipAnalysis.document_id == document_id))
        run_count = await db.scalar(select(func.count(ExtractionRun.id)).where(ExtractionRun.document_id == document_id))
        assert doc.processing_status == "relationship_extraction_completed"
        assert doc.processing_attempts == 1
        assert doc.relationship_extraction_attempts == 2
        assert {entity["mention_text"] for entity in final.payload["entities"]} == {"Entity Alpha", "Account Alpha", "Entity Beta", "Account Beta"}
        assert all(entity["source_references"] for entity in final.payload["entities"])
        assert len(final.payload["relationships"]) == 2
        assert all(relation["source_references"] for relation in final.payload["relationships"])
        assert run_count == 1
        assert ocr.recognized_text
        assert final.payload["run"]["cache_key"]
        assert final.payload["run"]["pipeline_version"] == relationship_extraction.EXTRACTION_PIPELINE_VERSION
        assert final.payload["run"]["stage_timings_ms"]["database_checkpoint_persistence_ms"] >= 0
    assert provider_calls == {"block_000001": 1, "block_000002": 4}
    async with app.state.test_sessions() as db:
        doc = await db.scalar(select(Document).where(Document.id == document_id))
        doc.processing_status = "relationship_extraction_pending"
        await db.commit()
    assert await ocr_worker.process_one()
    async with app.state.test_sessions() as db:
        doc = await db.scalar(select(Document).where(Document.id == document_id))
        assert doc.processing_status == "relationship_extraction_completed"
        assert doc.relationship_extraction_attempts == 2
    assert provider_calls == {"block_000001": 1, "block_000002": 4}
    auth_routes._attempts.clear()


@pytest.mark.asyncio
async def test_relationship_checkpoint_persistence_failure_is_retryable_without_ocr_rerun(client, monkeypatch, tmp_path):
    from sqlalchemy import select, func

    from app.models.entities import Document, ExtractionRun, OCRResult

    auth_routes._attempts.clear()
    monkeypatch.setattr(auth_routes, "settings", replace(settings, self_registration_enabled=True))
    monkeypatch.setattr(investigation_routes, "settings", replace(settings, document_storage_path=str(tmp_path)))
    monkeypatch.setattr(ocr_worker, "settings", replace(settings, document_storage_path=str(tmp_path), relationship_extraction_enabled=True))
    monkeypatch.setattr(ocr_worker, "SessionLocal", app.state.test_sessions)
    monkeypatch.setattr(relationship_extraction, "settings", replace(settings, llm_api_key="synthetic-token", llm_model="synthetic-model"))
    monkeypatch.setattr(ocr_worker, "_run_engine", lambda _path: ({"page_metadata": [], "engine_output": [{"page_index": 0, "parsing_res_list": [{"text": "Synthetic Person", "block_id": "engine-a"}]}]}, "test-engine"))
    monkeypatch.setattr(relationship_extraction, "chat_completion", lambda *_args, **_kwargs: {
        "content": json.dumps({"schema_version": "relationship_extraction_v1", "entities": [
            {"mention_id": "m1", "entity_type": "PERSON", "mention_text": "Synthetic Person", "source_block_ids": ["block_000001"], "confidence": None},
        ], "relationships": []}),
    })
    real_persist = ocr_worker._persist_relationship_chunk

    async def fail_checkpoint(*_args, **_kwargs):
        raise OSError("synthetic database write failure")

    monkeypatch.setattr(ocr_worker, "_persist_relationship_chunk", fail_checkpoint)
    await client.post("/api/v1/auth/register", json={"full_name": "Checkpoint Failure", "email": "checkpoint-failure@example.com", "password": PASSWORD})
    headers = {"X-CSRF-Token": client.cookies.get("dfi_csrf")}
    case = await client.post("/api/v1/investigations", headers=headers, json={"title": "Checkpoint persistence"})
    uploaded = await client.post(
        f"/api/v1/investigations/{case.json()['id']}/documents",
        headers={**headers, "X-File-Name": "evidence.png", "Content-Type": "image/png"},
        content=b"\x89PNG\r\n\x1a\nsource",
    )
    document_id = uuid.UUID(uploaded.json()["id"])
    assert await ocr_worker.process_one()
    async with app.state.test_sessions() as db:
        doc = await db.scalar(select(Document).where(Document.id == document_id))
        ocr = await db.scalar(select(OCRResult).where(OCRResult.document_id == document_id))
        assert doc.processing_status == "relationship_extraction_failed"
        assert doc.processing_error_code == "relationship_persistence_failed"
        assert doc.processing_error_retryable is True
        assert ocr.recognized_text == "Synthetic Person"

    monkeypatch.setattr(ocr_worker, "_persist_relationship_chunk", real_persist)
    retry = await client.post(f"/api/v1/investigations/{case.json()['id']}/documents/{document_id}/retry", headers=headers)
    assert retry.status_code == 202
    assert await ocr_worker.process_one()
    async with app.state.test_sessions() as db:
        doc = await db.scalar(select(Document).where(Document.id == document_id))
        ocr = await db.scalar(select(OCRResult).where(OCRResult.document_id == document_id))
        run_count = await db.scalar(select(func.count(ExtractionRun.id)).where(ExtractionRun.document_id == document_id))
        assert doc.processing_status == "relationship_extraction_completed"
        assert doc.processing_attempts == 1
        assert doc.relationship_extraction_attempts == 2
        assert ocr.recognized_text == "Synthetic Person"
        assert run_count == 1
    auth_routes._attempts.clear()
    monkeypatch.setattr(auth_routes, "settings", replace(settings, self_registration_enabled=True))
    monkeypatch.setattr(investigation_routes, "settings", replace(settings, document_storage_path=str(tmp_path)))
    monkeypatch.setattr(ocr_worker, "settings", replace(settings, document_storage_path=str(tmp_path), relationship_extraction_enabled=True))
    monkeypatch.setattr(ocr_worker, "SessionLocal", app.state.test_sessions)
    model_calls = []
    monkeypatch.setattr(relationship_extraction, "extract", lambda blocks, **_kwargs: (model_calls.append(blocks) or {"schema_version": "relationship_extraction_v1", "entities": [], "relationships": [], "chunks": []}, {"usage": []}))
    monkeypatch.setattr(ocr_worker, "_run_engine", lambda _path: ({"page_metadata": [], "engine_output": [{"page_index": 0, "parsing_res_list": [{"text": "Stored OCR evidence", "block_id": "source-1"}]}]}, "test-engine"))
    await client.post("/api/v1/auth/register", json={"full_name": "Retry Test", "email": "retry-relation@example.com", "password": PASSWORD})
    headers = {"X-CSRF-Token": client.cookies.get("dfi_csrf")}
    case = await client.post("/api/v1/investigations", headers=headers, json={"title": "Relationship retry"})
    uploaded = await client.post(
        f"/api/v1/investigations/{case.json()['id']}/documents",
        headers={**headers, "X-File-Name": "evidence.png", "Content-Type": "image/png"},
        content=b"\x89PNG\r\n\x1a\nsource",
    )
    document_id = uuid.UUID(uploaded.json()["id"])
    assert await ocr_worker.process_one()
    assert len(model_calls) == 1

    async with app.state.test_sessions() as db:
        doc = await db.scalar(select(Document).where(Document.id == document_id))
        doc.processing_status = "relationship_extraction_failed"
        doc.processing_error = "Safe synthetic failure"
        doc.processing_error_code = "invalid_model_output"
        doc.processing_error_retryable = True
        await db.commit()

    retry_response = await client.post(
        f"/api/v1/investigations/{case.json()['id']}/documents/{document_id}/retry",
        headers=headers,
    )
    assert retry_response.status_code == 202
    assert retry_response.json()["status"] == "relationship_extraction_pending"
    assert await ocr_worker.process_one()
    assert len(model_calls) == 2

    async with app.state.test_sessions() as db:
        doc = await db.scalar(select(Document).where(Document.id == document_id))
        ocr_result = await db.scalar(select(OCRResult).where(OCRResult.document_id == document_id))
        run_count = await db.scalar(select(func.count(ExtractionRun.id)).where(ExtractionRun.document_id == document_id))
        assert doc.processing_status == "relationship_extraction_completed"
        assert doc.processing_attempts == 1
        assert doc.relationship_extraction_attempts == 2
        assert ocr_result.recognized_text == "Stored OCR evidence"
        assert run_count == 1
    auth_routes._attempts.clear()
    monkeypatch.setattr(auth_routes, "settings", replace(settings, self_registration_enabled=True))
    monkeypatch.setattr(investigation_routes, "settings", replace(settings, document_storage_path=str(tmp_path)))
    monkeypatch.setattr(ocr_worker, "settings", replace(settings, document_storage_path=str(tmp_path)))
    monkeypatch.setattr(ocr_worker, "SessionLocal", app.state.test_sessions)
    monkeypatch.setattr(relationship_extraction, "extract", lambda _blocks, **_kwargs: ({"schema_version": "relationship_extraction_v1", "entities": [], "relationships": [], "chunks": []}, {"usage": []}))
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
    assert audit_actions[-3:] == ["extraction.completed", "relationship_extraction.started", "relationship_extraction.completed"]
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
    assert blocks == [{"block_id": "block_000001", "source_path": "root[0].parsing_res_list[0]", "text": "ACME Ltd", "extraction_method": "ocr", "page_index": 2, "engine_block_id": "blk-7", "engine_bbox": [1, 2, 3, 4], "engine_confidence": 0.91}]


def test_ocr_block_adapter_reads_ppstructure_payload():
    raw = [{"res": {"parsing_res_list": [
        {"block_label": "doc_title", "block_content": "ACME Ltd", "block_bbox": [1, 2, 3, 4], "block_id": 0, "block_order": 1},
        {"block_label": "image", "block_content": "", "block_bbox": [0, 0, 1, 1], "block_id": 1, "block_order": None},
    ]}}]
    blocks = _blocks(raw)
    assert blocks == [{"block_id": "block_000001", "source_path": "root[0].res.parsing_res_list[0]", "text": "ACME Ltd", "extraction_method": "ocr", "engine_block_id": 0, "engine_bbox": [1, 2, 3, 4], "engine_label": "doc_title"}]


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


@pytest.mark.asyncio
async def test_deterministic_candidates_are_committed_before_ai_failure(client, monkeypatch):
    from sqlalchemy import select

    from app.models.entities import Document, OCRResult, RelationshipAnalysis

    auth_routes._attempts.clear()
    monkeypatch.setattr(auth_routes, "settings", replace(settings, self_registration_enabled=True))
    monkeypatch.setattr(ocr_worker, "settings", replace(settings, relationship_extraction_enabled=True))
    monkeypatch.setattr(ocr_worker, "SessionLocal", app.state.test_sessions)
    monkeypatch.setattr(relationship_extraction, "settings", replace(settings, llm_api_key="synthetic-token", llm_model="test-model"))
    await client.post("/api/v1/auth/register", json={
        "full_name": "Deterministic Stage Test", "email": "deterministic-stage@example.com", "password": PASSWORD,
    })
    headers = {"X-CSRF-Token": client.cookies.get("dfi_csrf")}
    case = await client.post("/api/v1/investigations", headers=headers, json={"title": "Deterministic stage isolation"})
    investigation_id = uuid.UUID(case.json()["id"])
    previous_id = uuid.uuid4()
    current_id = uuid.uuid4()
    shared_email = "shared-identity@example.test"
    async with app.state.test_sessions() as db:
        for index, document_id in enumerate((previous_id, current_id)):
            document = Document(
                id=document_id, investigation_id=investigation_id, filename=f"source-{index}.pdf",
                content_type="application/pdf", size_bytes=1, storage_key=uuid.uuid4().hex,
                sha256=hashlib.sha256(f"source-{index}".encode()).hexdigest(),
                processing_status="relationship_extraction_pending" if document_id == current_id else "ocr_completed",
            )
            db.add(document)
            db.add(OCRResult(
                document_id=document_id, provider="test", provider_version="1", raw_result={},
                recognized_text=f"Email: {shared_email}",
                blocks=[{"block_id": f"email-{index}", "page_index": index, "extraction_method": "native_pdf_text", "text": f"Email: {shared_email}"}],
            ))
        await db.commit()

    # The earlier document is already OCR-complete in this fixture, so seed its
    # deterministic index exactly as the OCR worker does before processing the
    # current document. OCR rows alone are not indexed relationship evidence.
    await ocr_worker._persist_deterministic_evidence(previous_id)

    def fail_ai(*_args, **_kwargs):
        raise relationship_extraction.RelationshipExtractionError(
            "invalid_model_output", "Synthetic invalid response", retryable=False,
        )

    monkeypatch.setattr(relationship_extraction, "extract", fail_ai)
    await ocr_worker._run_relationship_extraction(current_id)

    async with app.state.test_sessions() as db:
        document = await db.scalar(select(Document).where(Document.id == current_id))
        relationship = await db.scalar(select(RelationshipAnalysis).where(RelationshipAnalysis.document_id == current_id))
        assert document.processing_status == "relationship_extraction_failed"
        assert relationship.payload["analysis_stages"] == {
            "source_extraction": "completed", "deterministic_evidence": "completed",
            "investigation_correlation": "completed", "ai_enrichment": "failed",
            "investigator_review": "pending",
        }
        assert relationship.payload["deterministic_analysis"]["entity_count"] == 1
        assert relationship.payload["deterministic_analysis"]["relationship_candidate_count"] == 1
        candidate = relationship.payload["deterministic_analysis"]["relationship_candidates"][0]
        assert str(document.id) in {ref["document_id"] for ref in candidate["evidence_refs"]}
        assert len({ref["document_id"] for ref in candidate["evidence_refs"]}) == 2
        expected_refs = {
            str(previous_id): ("email-0", hashlib.sha256(b"source-0").hexdigest()),
            str(current_id): ("email-1", hashlib.sha256(b"source-1").hexdigest()),
        }
        assert len(candidate["evidence_refs"]) == 2
        for ref in candidate["evidence_refs"]:
            block_id, source_sha256 = expected_refs[ref["document_id"]]
            assert ref["block_id"] == block_id
            assert ref["quoted_text"] == ref["raw_value"] == shared_email
            assert ref["source_sha256"] == source_sha256

    documents_response = await client.get(f"/api/v1/investigations/{investigation_id}/documents")
    current_response = next(item for item in documents_response.json() if item["id"] == str(current_id))
    assert current_response["analysis_stages"]["deterministic_evidence"] == "completed"
    assert current_response["analysis_stages"]["ai_enrichment"] == "failed"
    auth_routes._attempts.clear()

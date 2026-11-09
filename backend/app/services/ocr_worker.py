"""Durable database-polled OCR worker for local/single-worker deployments."""
import atexit
import json
import os
import subprocess
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

from sqlalchemy import func, select

from app.core.config import settings
from app.db.session import SessionLocal
from app.models.entities import Document, EvidencePage, EvidenceSpan, ExtractionRun, OCRResult, RelationshipAnalysis
from app.services.audit_chain import append_audit

REPO_ROOT = Path(__file__).resolve().parents[3]
BACKEND_ROOT = Path(__file__).resolve().parents[2]
OCR_PROJECT = Path(os.getenv("PADDLEOCR_PROJECT_PATH", str(REPO_ROOT.parent / "extra_things" / "PaddleOcr" / "ocr_evaluation"))).resolve()
SERVICE_SCRIPT = BACKEND_ROOT / "scripts" / "paddle_service.py"
OCR_TIMEOUT_SECONDS = 600
OCR_INIT_TIMEOUT_SECONDS = 900

_server: subprocess.Popen | None = None
_server_lock = threading.Lock()


def _paddle_python() -> str:
    if os.name != "nt":
        return os.getenv("PADDLEOCR_PYTHON", str(OCR_PROJECT / ".venv" / "bin" / "python"))
    return os.getenv("PADDLEOCR_PYTHON", str(OCR_PROJECT / ".venv" / "Scripts" / "python.exe"))


def _stop_server() -> None:
    global _server
    server, _server = _server, None
    if server is not None and server.poll() is None:
        server.terminate()
        try:
            server.wait(timeout=10)
        except subprocess.TimeoutExpired:
            server.kill()


atexit.register(_stop_server)


def _read_line(stream, timeout: float) -> str | None:
    result: dict[str, str] = {}

    def read() -> None:
        result["line"] = stream.readline()

    reader = threading.Thread(target=read, daemon=True)
    reader.start()
    reader.join(timeout)
    if reader.is_alive():
        return None
    return result.get("line")


def _start_server() -> subprocess.Popen:
    python = _paddle_python()
    if not Path(python).is_file():
        raise RuntimeError("PaddleOCR environment is not configured; see OCR setup instructions")
    if not SERVICE_SCRIPT.is_file():
        raise RuntimeError("PaddleOCR service script is missing from the backend")
    env = os.environ.copy()
    env.setdefault("DISABLE_MODEL_SOURCE_CHECK", "True")
    env["PYTHONUNBUFFERED"] = "1"
    server = subprocess.Popen(
        [python, str(SERVICE_SCRIPT)],
        cwd=str(OCR_PROJECT),
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        bufsize=1,
        env=env,
    )
    line = _read_line(server.stdout, OCR_INIT_TIMEOUT_SECONDS)
    try:
        handshake = json.loads(line) if line else None
    except ValueError:
        handshake = None
    if not isinstance(handshake, dict) or not handshake.get("ok"):
        error = handshake.get("error") if isinstance(handshake, dict) else None
        server.kill()
        raise RuntimeError(error or "PaddleOCR service failed to start")
    return server


def _ensure_server() -> subprocess.Popen:
    global _server
    if _server is None or _server.poll() is not None:
        _server = _start_server()
    return _server


def warm_up() -> None:
    with _server_lock:
        _ensure_server()


def _run_engine(path: Path) -> tuple[dict | list, str]:
    with _server_lock:
        server = _ensure_server()
        try:
            server.stdin.write(json.dumps({"path": str(path)}) + "\n")
            server.stdin.flush()
            line = _read_line(server.stdout, OCR_TIMEOUT_SECONDS)
            if line is None:
                raise RuntimeError("PaddleOCR did not return a result before the timeout")
            payload = json.loads(line)
            if not payload.get("ok"):
                raise RuntimeError(payload.get("error") or "PaddleOCR execution failed")
            raw_result = payload["result"]
            if payload.get("page_metadata"):
                raw_result = {
                    "engine_output": raw_result,
                    "page_metadata": payload["page_metadata"],
                }
            return raw_result, "3.3.2"
        except Exception:
            _stop_server()
            raise


def _blocks(raw: object) -> list[dict]:
    blocks: list[dict] = []

    def add(text: object, source_path: str, *, label: object = None, bbox: object = None,
            confidence: object = None, engine_block_id: object = None, page_index: int | None = None) -> None:
        if not isinstance(text, str) or not text.strip():
            return
        block = {"block_id": f"block_{len(blocks)+1:06d}", "source_path": source_path, "text": text.strip()}
        if page_index is not None:
            block["page_index"] = page_index
        if isinstance(engine_block_id, (str, int)):
            block["engine_block_id"] = engine_block_id
        if isinstance(bbox, (list, dict)):
            block["engine_bbox"] = bbox
        if isinstance(confidence, (int, float)) and not isinstance(confidence, bool):
            block["engine_confidence"] = confidence
        if isinstance(label, str) and label:
            block["engine_label"] = label
        blocks.append(block)

    def visit(value: object, path: str = "root", page_index: int | None = None) -> None:
        if isinstance(value, str):
            try:
                decoded = json.loads(value)
            except (ValueError, TypeError):
                return
            visit(decoded, path, page_index)
        elif isinstance(value, dict):
            current_page = value.get("page_index", value.get("page_number", page_index))
            if not isinstance(current_page, int):
                current_page = page_index
            handled = False
            parsing = value.get("parsing_res_list")
            if isinstance(parsing, list):
                started_at = len(blocks)
                for index, item in enumerate(parsing):
                    if not isinstance(item, dict):
                        continue
                    item_page = item.get("page_index", current_page)
                    if not isinstance(item_page, int):
                        item_page = current_page
                    add(item.get("block_content", item.get("text")), f"{path}.parsing_res_list[{index}]",
                        label=item.get("block_label", item.get("label")),
                        bbox=item.get("block_bbox", item.get("bbox")),
                        confidence=item.get("confidence", item.get("score")),
                        engine_block_id=item.get("block_id"), page_index=item_page)
                if len(blocks) > started_at:
                    handled = True
            ocr = value.get("overall_ocr_res")
            if not handled and isinstance(ocr, dict) and isinstance(ocr.get("rec_texts"), list):
                boxes = ocr.get("rec_boxes") or ocr.get("rec_polys") or []
                scores = ocr.get("rec_scores") or []
                for index, text in enumerate(ocr["rec_texts"]):
                    add(text, f"{path}.overall_ocr_res.rec_texts[{index}]", page_index=current_page,
                        bbox=boxes[index] if index < len(boxes) else None,
                        confidence=scores[index] if index < len(scores) else None)
                handled = True
            text = value.get("text")
            if not handled and isinstance(text, str) and text.strip():
                block = {"block_id": f"block_{len(blocks)+1:06d}", "source_path": path, "text": text}
                if current_page is not None:
                    block["page_index"] = current_page
                for key in ("id", "block_id", "bbox", "box", "confidence", "score", "page_index", "page_number", "label"):
                    if key in value and isinstance(value[key], (str, int, float, list, dict)):
                        block[f"engine_{key}"] = value[key]
                blocks.append(block)
                handled = True
            for key, child in value.items():
                if key == "text" or (handled and key in {"parsing_res_list", "overall_ocr_res"}):
                    continue
                visit(child, f"{path}.{key}", current_page)
        elif isinstance(value, list):
            for index, child in enumerate(value):
                visit(child, f"{path}[{index}]", page_index)
    visit(raw)
    return blocks


async def _mark_failed(doc_id, message: str, extraction_run_id=None) -> None:
    async with SessionLocal() as db:
        current = await db.scalar(select(Document).where(Document.id == doc_id))
        if current:
            current.processing_status = "failed"
            current.processing_error = message[:500]
            current.processing_completed_at = datetime.now(timezone.utc)
            if extraction_run_id:
                run = await db.scalar(select(ExtractionRun).where(ExtractionRun.id == extraction_run_id))
                if run:
                    run.status = "failed"
                    run.error_detail = message[:2000]
                    run.completed_at = current.processing_completed_at
                    await append_audit(
                        db,
                        investigation_id=current.investigation_id,
                        actor_user_id=None,
                        action="extraction.failed",
                        target_type="extraction_run",
                        target_id=str(run.id),
                        details={"provider": run.provider, "error": message[:300]},
                    )
            await db.commit()


async def process_one() -> bool:
    async with SessionLocal() as db:
        expired_at = datetime.now(timezone.utc) - timedelta(minutes=15)
        stale = (await db.scalars(select(Document).where(
            Document.processing_status.in_(["processing", "relationship_extraction_pending"]),
            Document.processing_started_at < expired_at,
        ).with_for_update(skip_locked=True))).all()
        for item in stale:
            item.processing_status = "failed"
            item.processing_error = "Processing worker stopped before completion"
            item.processing_completed_at = datetime.now(timezone.utc)
        if stale:
            await db.commit()
        doc = await db.scalar(select(Document).where(Document.processing_status == "queued").order_by(Document.created_at).with_for_update(skip_locked=True).limit(1))
        if doc is None:
            return False
        if doc.processing_attempts >= 3:
            doc.processing_status = "failed"
            doc.processing_error = "Maximum processing attempts reached"
            await db.commit()
            return True
        doc.processing_attempts += 1
        doc.processing_status = "processing"
        doc.processing_started_at = datetime.now(timezone.utc)
        run_number = (await db.scalar(
            select(func.max(ExtractionRun.run_number)).where(ExtractionRun.document_id == doc.id)
        ) or 0) + 1
        extraction_run = ExtractionRun(
            document_id=doc.id,
            run_number=run_number,
            provider="paddleocr_ppstructurev3",
            provider_version=None,
            configuration={
                "engine": "PP-StructureV3",
                "layout_model": os.getenv("PADDLEOCR_LAYOUT_MODEL", "PP-DocLayout_plus-L"),
                "detection_model": os.getenv("PADDLEOCR_DET_MODEL", "PP-OCRv5_mobile_det"),
                "recognition_model": os.getenv("PADDLEOCR_REC_MODEL", "PP-OCRv5_mobile_rec"),
                "preprocessing": "engine_default",
                "fallback_engine": "unavailable",
            },
            status="processing",
            started_at=doc.processing_started_at,
        )
        db.add(extraction_run)
        await db.flush()
        extraction_run_id = extraction_run.id
        await append_audit(
            db,
            investigation_id=doc.investigation_id,
            actor_user_id=None,
            action="extraction.started",
            target_type="extraction_run",
            target_id=str(extraction_run.id),
            details={"document_id": str(doc.id), "run_number": run_number},
        )
        print(f"Claimed document {doc.id} for OCR (attempt {doc.processing_attempts}/3)", flush=True)
        await db.commit()
        storage_key = doc.storage_key
        doc_id = doc.id
    try:
        async with SessionLocal() as db:
            source = await db.scalar(select(Document).where(Document.id == doc_id))
            if source is None or not storage_key:
                raise RuntimeError("Stored source file is unavailable")
            path = Path(settings.document_storage_path).resolve() / storage_key
        started = time.perf_counter()
        raw, provider = _run_engine(path)
        latency_ms = (time.perf_counter() - started) * 1000
        source_blocks = _blocks(raw)
        async with SessionLocal() as db:
            current = await db.scalar(select(Document).where(Document.id == doc_id))
            if current is None:
                return True
            extraction = await db.scalar(select(ExtractionRun).where(ExtractionRun.id == extraction_run_id))
            if extraction is None:
                raise RuntimeError("OCR extraction run disappeared before results were saved")
            page_blocks: dict[int, list[dict]] = {}
            for block in source_blocks:
                page_index = block.get("page_index")
                if not isinstance(page_index, int) or isinstance(page_index, bool) or page_index < 0:
                    page_index = 0
                page_blocks.setdefault(page_index, []).append(block)
            page_metadata = {}
            if isinstance(raw, dict) and isinstance(raw.get("page_metadata"), list):
                for metadata in raw["page_metadata"]:
                    if isinstance(metadata, dict) and isinstance(metadata.get("page_number"), int):
                        page_metadata[metadata["page_number"] - 1] = metadata
            page_records: dict[int, EvidencePage] = {}
            page_indexes = sorted(set(page_blocks) | set(page_metadata))
            for page_index in page_indexes:
                blocks = page_blocks.get(page_index, [])
                metadata = page_metadata.get(page_index, {})
                page = EvidencePage(
                    document_id=doc_id,
                    extraction_run_id=extraction_run_id,
                    page_number=page_index + 1,
                    width=metadata.get("width"),
                    height=metadata.get("height"),
                    text="\n".join(block["text"] for block in blocks),
                )
                db.add(page)
                page_records[page_index] = page
            await db.flush()
            for block in source_blocks:
                page_index = block.get("page_index")
                if not isinstance(page_index, int) or isinstance(page_index, bool) or page_index < 0:
                    page_index = 0
                db.add(EvidenceSpan(
                    document_id=doc_id,
                    extraction_run_id=extraction_run_id,
                    page_id=page_records.get(page_index).id if page_index in page_records else None,
                    block_id=str(block["block_id"]),
                    text=block["text"],
                    bounding_box=block.get("engine_bbox"),
                    confidence=block.get("engine_confidence"),
                    source_reference=str(block.get("source_path") or block["block_id"]),
                ))
            record = await db.scalar(select(OCRResult).where(OCRResult.document_id == doc_id))
            fields = dict(provider="paddleocr_ppstructurev3", provider_version=provider, raw_result=raw, blocks=source_blocks, recognized_text="\n".join(b["text"] for b in source_blocks))
            if record:
                for key, value in fields.items():
                    setattr(record, key, value)
            else:
                db.add(OCRResult(document_id=doc_id, **fields))
            extraction.provider_version = provider
            extraction.raw_result = raw
            extraction.latency_ms = latency_ms
            extraction.status = "completed"
            extraction.completed_at = datetime.now(timezone.utc)
            current.processing_status = "ocr_completed"
            current.processing_error = None
            current.processing_completed_at = datetime.now(timezone.utc)
            await append_audit(
                db,
                investigation_id=current.investigation_id,
                actor_user_id=None,
                action="extraction.completed",
                target_type="extraction_run",
                target_id=str(extraction.id),
                details={"provider": extraction.provider, "provider_version": provider, "latency_ms": round(latency_ms, 2), "span_count": len(source_blocks)},
            )
            await db.commit()
        print(f"OCR completed for document {doc_id} ({len(source_blocks)} blocks)", flush=True)
    except Exception as exc:
        await _mark_failed(doc_id, str(exc), extraction_run_id)
        print(f"Document {doc_id} OCR failed: {str(exc)[:300]}", flush=True)
        return True

    if not settings.relationship_extraction_enabled:
        return True
    async with SessionLocal() as db:
        current = await db.scalar(select(Document).where(Document.id == doc_id))
        if current is None:
            return True
        current.processing_status = "relationship_extraction_pending"
        await db.commit()
    try:
        from app.services.relationship_extraction import extract
        graph, metadata = extract(source_blocks)
        graph["usage"] = metadata.get("usage", [])
        async with SessionLocal() as db:
            current = await db.scalar(select(Document).where(Document.id == doc_id))
            if current:
                record = await db.scalar(select(RelationshipAnalysis).where(RelationshipAnalysis.document_id == doc_id))
                values = {"model": settings.xai_model, "payload": graph}
                if record:
                    record.model = values["model"]
                    record.payload = values["payload"]
                else:
                    db.add(RelationshipAnalysis(document_id=doc_id, **values))
                current.processing_status = "relationship_extraction_completed"
                current.processing_error = None
                current.processing_completed_at = datetime.now(timezone.utc)
                await append_audit(
                    db,
                    investigation_id=current.investigation_id,
                    actor_user_id=None,
                    action="relationship_extraction.completed",
                    target_type="document",
                    target_id=str(current.id),
                    details={"model": settings.xai_model},
                )
                await db.commit()
        print(f"Relationship extraction completed for document {doc_id}", flush=True)
    except Exception as exc:
        async with SessionLocal() as db:
            current = await db.scalar(select(Document).where(Document.id == doc_id))
            if current:
                current.processing_status = "relationship_extraction_failed"
                current.processing_error = str(exc)[:500]
                current.processing_completed_at = datetime.now(timezone.utc)
                await append_audit(
                    db,
                    investigation_id=current.investigation_id,
                    actor_user_id=None,
                    action="relationship_extraction.failed",
                    target_type="document",
                    target_id=str(current.id),
                    details={"error": str(exc)[:300]},
                )
                await db.commit()
        print(f"Document {doc_id} relationship extraction failed: {str(exc)[:300]}", flush=True)
    return True

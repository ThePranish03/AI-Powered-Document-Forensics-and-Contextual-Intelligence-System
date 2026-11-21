"""Durable database-polled OCR worker for local/single-worker deployments."""
import atexit
import json
import asyncio
import os
import subprocess
import threading
import time
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

import fitz

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
STALE_JOB_TIMEOUT_SECONDS = OCR_INIT_TIMEOUT_SECONDS + OCR_TIMEOUT_SECONDS + 300


class _RelationshipPersistenceError(RuntimeError):
    def __init__(self, exception_type: str):
        self.exception_type = exception_type
        super().__init__("relationship chunk persistence failed")

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


def _native_pdf_blocks(path: Path) -> list[dict]:
    """Extract selectable PDF text as separate, source-linked observations.

    OCR continues to run on every page. Native spans supplement that output and
    retain their own method, coordinates, and stable page-local identifiers.
    """
    if not _is_pdf_file(path):
        return []
    blocks: list[dict] = []
    with fitz.open(path) as document:
        for page_index, page in enumerate(document):
            detected_tables = page.find_tables().tables
            table_bounds = [fitz.Rect(table.bbox) for table in detected_tables if table.bbox]
            raw_blocks = page.get_text("dict", sort=False).get("blocks", [])
            page_block_index = 0
            for raw_block in raw_blocks:
                lines = raw_block.get("lines")
                if not isinstance(lines, list):
                    continue
                text = "\n".join(
                    "".join(str(span.get("text") or "") for span in line.get("spans", []))
                    for line in lines
                ).strip()
                if not text:
                    continue
                bbox = raw_block.get("bbox")
                if isinstance(bbox, (list, tuple)) and len(bbox) == 4:
                    region = fitz.Rect(bbox)
                    if any(region.intersects(table_box) and region.get_area() > 0 and table_box.get_area() > 0
                           and region.intersect(table_box).get_area() / min(region.get_area(), table_box.get_area()) >= 0.5
                           for table_box in table_bounds):
                        continue
                page_block_index += 1
                blocks.append({
                    "block_id": f"native_p{page_index + 1:04d}_b{page_block_index:05d}",
                    "source_path": f"page[{page_index}].native_text.blocks[{page_block_index - 1}]",
                    "text": text,
                    "page_index": page_index,
                    "engine_bbox": list(bbox) if isinstance(bbox, (list, tuple)) and len(bbox) == 4 else None,
                    "bbox_space": "pdf_points",
                    "extraction_method": "native_pdf_text",
                    "engine_confidence": None,
                })
            for table_index, table in enumerate(detected_tables, start=1):
                extracted_rows = table.extract()
                headers = [" ".join(str(value or "").split()) for value in (table.header.names or [])]
                known_headers = {
                    "full name", "role", "role / title", "identifier", "ownership %", "tx id", "transaction id",
                    "date", "date (utc)", "originator", "beneficiary entity", "beneficiary account", "amount", "amount (usd)",
                    "approver", "session id", "timestamp", "timestamp (utc)", "user account", "source ip", "target resource accessed",
                }
                headers_are_grounded = any(" ".join(header.casefold().replace("\n", " ").split()) in known_headers for header in headers)
                if not headers_are_grounded:
                    headers = []
                rows = extracted_rows[1:] if headers_are_grounded else extracted_rows
                row_offset = 1 if headers_are_grounded else 0
                for row_index, row in enumerate(rows, start=row_offset):
                    cells = []
                    for column_index, raw_value in enumerate(row):
                        value = " ".join(str(raw_value or "").split())
                        if not value:
                            continue
                        cell_box = None
                        try:
                            raw_box = table.rows[row_index].cells[column_index]
                            if raw_box:
                                cell_box = [round(float(item), 3) for item in raw_box]
                        except (IndexError, TypeError, ValueError):
                            cell_box = None
                        cells.append({
                            "column_index": column_index,
                            "header": headers[column_index] if column_index < len(headers) else None,
                            "raw_text": value,
                            "bbox": cell_box,
                        })
                    if not cells:
                        continue
                    page_block_index += 1
                    row_text = " | ".join(
                        "{}: {}".format(cell.get("header") or f"column_{cell['column_index'] + 1}", cell["raw_text"])
                        for cell in cells
                    )
                    row_box = table.rows[row_index].bbox if hasattr(table.rows[row_index], "bbox") else table.bbox
                    blocks.append({
                        "block_id": f"native_p{page_index + 1:04d}_table{table_index:03d}_row{row_index:04d}",
                        "source_path": f"page[{page_index}].native_text.tables[{table_index - 1}].rows[{row_index}]",
                        "text": row_text,
                        "page_index": page_index,
                        "engine_bbox": [round(float(item), 3) for item in row_box] if row_box else list(table.bbox),
                        "bbox_space": "pdf_points",
                        "extraction_method": "native_pdf_table",
                        "engine_confidence": None,
                        "table_structure_status": "detected" if headers_are_grounded else "uncertain",
                        "table_index": table_index - 1,
                        "row_index": row_index,
                        "cells": cells,
                    })
    return blocks


def _is_pdf_file(path: Path) -> bool:
    """Recognize PDFs by content because secure upload keys have no suffix."""
    if path.suffix.lower() == ".pdf":
        return True
    try:
        with path.open("rb") as source:
            return source.read(5) == b"%PDF-"
    except OSError:
        return False


def _blocks(raw: object) -> list[dict]:
    blocks: list[dict] = []

    def add(text: object, source_path: str, *, label: object = None, bbox: object = None,
            confidence: object = None, engine_block_id: object = None, page_index: int | None = None) -> None:
        if not isinstance(text, str) or not text.strip():
            return
        block = {"block_id": f"block_{len(blocks)+1:06d}", "source_path": source_path, "text": text.strip(), "extraction_method": "ocr"}
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


def _logical_text(blocks: list[dict]) -> str:
    """Build search text without counting identical OCR/native blocks twice."""
    seen: set[str] = set()
    text: list[str] = []
    for block in blocks:
        value = str(block.get("text") or "").strip()
        key = " ".join(value.casefold().split())
        if value and key not in seen:
            seen.add(key)
            text.append(value)
    return "\n".join(text)


async def _mark_failed(doc_id, message: str, extraction_run_id=None) -> None:
    async with SessionLocal() as db:
        current = await db.scalar(select(Document).where(Document.id == doc_id).with_for_update())
        if current:
            current.processing_status = "failed"
            current.processing_error = "OCR extraction failed. Check the extraction run diagnostics."
            current.processing_error_code = "ocr_extraction_failed"
            current.processing_error_retryable = True
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


def _relationship_blocks(blocks: list[dict]) -> list[dict]:
    seen_ids: set[str] = set()
    result: list[dict] = []
    for block in blocks:
        block_id = str(block.get("block_id") or "")
        if not block_id or not str(block.get("text") or "").strip() or block_id in seen_ids:
            continue
        seen_ids.add(block_id)
        result.append(block)
    return result


async def _persist_deterministic_evidence(doc_id) -> dict:
    """Persist deterministic source evidence before optional AI enrichment."""
    from app.services.deterministic_relationships import _mentions_from_facts
    from app.services.ocr_metrics import build_cross_document_correlation, extract_document_facts

    async with SessionLocal() as db:
        current = await db.scalar(select(Document).where(Document.id == doc_id))
        if current is None:
            raise RuntimeError("Document disappeared before deterministic analysis")
        investigation_id = current.investigation_id
        source_sha256 = current.sha256
        rows = (await db.execute(
            select(Document, OCRResult, RelationshipAnalysis)
            .outerjoin(OCRResult, OCRResult.document_id == Document.id)
            .outerjoin(RelationshipAnalysis, RelationshipAnalysis.document_id == Document.id)
            .where(Document.investigation_id == investigation_id)
            .order_by(Document.created_at, Document.id)
        )).all()

    documents = [{
        "document_id": str(document.id), "filename": document.filename,
        "sha256": document.sha256,
        "provider": ocr.provider if ocr else None,
        "provider_version": ocr.provider_version if ocr else None,
        "blocks": ocr.blocks if ocr and isinstance(ocr.blocks, list) else [],
        "text": ocr.recognized_text if ocr else "",
        "processing_status": document.processing_status,
        "structured_extraction": analysis.payload if analysis else None,
    } for document, ocr, analysis in rows]
    facts = [extract_document_facts(document) for document in documents]
    current_facts = next((fact for fact in facts if fact["document_id"] == str(doc_id)), None)
    if current_facts is None:
        raise RuntimeError("Current document is missing from its investigation evidence set")
    correlation = build_cross_document_correlation(documents)
    candidates = [
        item for item in correlation.get("links", [])
        if item.get("method") == "deterministic_relationship_candidate_v1"
        and str(doc_id) in item.get("document_ids", [])
    ]
    mentions = _mentions_from_facts([current_facts])
    completed_at = datetime.now(timezone.utc)

    async with SessionLocal() as db:
        current = await db.scalar(select(Document).where(Document.id == doc_id).with_for_update())
        if current is None or current.sha256 != source_sha256:
            raise RuntimeError("Source document changed during deterministic analysis")
        record = await db.scalar(select(RelationshipAnalysis).where(RelationshipAnalysis.document_id == doc_id))
        payload = dict(record.payload) if record and isinstance(record.payload, dict) else {}
        deterministic = {
            "status": "completed", "correlation_status": "completed",
            "pipeline_version": "deterministic_relationship_candidate_v1",
            "source_sha256": source_sha256, "completed_at": completed_at.isoformat(),
            "entity_count": len(mentions), "relationship_candidate_count": len(candidates),
            "entities": mentions, "relationship_candidates": candidates,
        }
        payload["deterministic_analysis"] = deterministic
        payload["analysis_stages"] = {
            "source_extraction": "completed", "deterministic_evidence": "completed",
            "investigation_correlation": "completed", "ai_enrichment": "pending",
            "investigator_review": "pending",
        }
        if record:
            record.payload = payload
        else:
            db.add(RelationshipAnalysis(document_id=doc_id, model=settings.llm_model, payload=payload))
        await append_audit(
            db, investigation_id=investigation_id, actor_user_id=None,
            action="deterministic_relationships.completed", target_type="document",
            target_id=str(doc_id), details={
                "source_sha256": source_sha256,
                "pipeline_version": deterministic["pipeline_version"],
                "entity_count": len(mentions),
                "relationship_candidate_count": len(candidates),
                "documents_considered": len(documents),
            },
        )
        await db.commit()
    return deterministic


async def _persist_relationship_failure(doc_id, run_id, error, *, started_at, input_blocks, input_characters):
    async with SessionLocal() as db:
        current = await db.scalar(select(Document).where(Document.id == doc_id))
        if current is None:
            return
        completed_at = datetime.now(timezone.utc)
        duration_ms = round((completed_at - started_at).total_seconds() * 1000, 2)
        current.processing_status = "relationship_extraction_failed"
        current.processing_error = str(error)[:500]
        current.processing_error_code = error.code
        current.processing_error_retryable = error.retryable
        current.processing_completed_at = completed_at
        partial = await db.scalar(select(RelationshipAnalysis).where(RelationshipAnalysis.document_id == doc_id))
        if partial and isinstance(partial.payload, dict):
            payload = dict(partial.payload)
            if isinstance(payload.get("run"), dict):
                run_metadata = dict(payload["run"])
                run_metadata["status"] = "partial" if payload.get("chunks") else "failed"
                run_metadata["error_code"] = error.code
                run_metadata["failed_chunk_id"] = error.failed_chunk_id
                payload["run"] = run_metadata
            stages = dict(payload.get("analysis_stages") or {})
            stages["ai_enrichment"] = "failed"
            payload["analysis_stages"] = stages
            partial.payload = payload
        await append_audit(
            db,
            investigation_id=current.investigation_id,
            actor_user_id=None,
            action="relationship_extraction.failed",
            target_type="document",
            target_id=str(current.id),
            details={
                "run_id": str(run_id), "error_code": error.code,
                "retryable": error.retryable, "diagnostics": error.diagnostics,
                "duration_ms": duration_ms, "input_block_count": input_blocks,
                "input_characters": input_characters, "model": settings.llm_model,
                "failed_chunk_id": error.failed_chunk_id,
            },
        )
        await db.commit()
    print(
        f"Relationship extraction failed for document {doc_id} "
        f"(code={error.code}, retryable={error.retryable}, duration_ms={duration_ms})",
        flush=True,
    )


async def _persist_relationship_chunk(doc_id, investigation_id, source_sha256, model, run_id, chunk, block_by_id):
    from app.services.relationship_extraction import EXTRACTION_PIPELINE_VERSION, _merge_relationship_chunks

    chunk["run_id"] = str(run_id)
    async with SessionLocal() as db:
        current = await db.scalar(select(Document).where(Document.id == doc_id))
        if current is None or current.sha256 != source_sha256:
            raise RuntimeError("Source document changed during relationship extraction")
        record = await db.scalar(select(RelationshipAnalysis).where(RelationshipAnalysis.document_id == doc_id))
        previous = record.payload if record and isinstance(record.payload, dict) else {}
        deterministic = previous.get("deterministic_analysis")
        previous_run = previous.get("run") if isinstance(previous.get("run"), dict) else {}
        if (
            previous_run.get("source_sha256") != source_sha256
            or previous.get("model") != model
            or previous.get("pipeline_version") != EXTRACTION_PIPELINE_VERSION
            or previous_run.get("status") != "partial"
        ):
            previous = {}
        chunk_map = {
            item.get("chunk_id"): item
            for item in previous.get("chunks", [])
            if isinstance(item, dict) and item.get("chunk_id") and item.get("status") == "completed"
        }
        chunk_map[chunk["chunk_id"]] = chunk
        chunks = list(chunk_map.values())
        entities, relationships = _merge_relationship_chunks(chunks)
        chunks_by_id = {item["chunk_id"]: item for item in chunks}

        def make_references(block_ids, chunk_ids):
            refs = []
            for block_id in block_ids:
                block = block_by_id.get(str(block_id))
                if not block:
                    continue
                applicable = [chunks_by_id[cid] for cid in chunk_ids if cid in chunks_by_id and block_id in chunks_by_id[cid].get("input_block_ids", [])]
                for item in applicable or [None]:
                    page_index = block.get("page_index")
                    refs.append({
                        "document_id": str(doc_id), "investigation_id": str(investigation_id),
                        "run_id": item.get("run_id") if item else str(run_id),
                        "source_sha256": source_sha256, "block_id": str(block_id),
                        "page_index": page_index,
                        "page_number": page_index + 1 if isinstance(page_index, int) and not isinstance(page_index, bool) else None,
                        "extraction_method": block.get("extraction_method", "ocr"),
                        "coordinate_space": block.get("bbox_space"),
                        "bounding_box": block.get("engine_bbox"), "quoted_text": block.get("text"),
                    })
            return refs

        for entity in entities:
            entity["source_references"] = make_references(entity.get("source_block_ids", []), entity.get("chunk_ids", []))
            entity["provenance"] = {"document_id": str(doc_id), "investigation_id": str(investigation_id), "run_ids": [chunks_by_id[cid].get("run_id") for cid in entity.get("chunk_ids", []) if cid in chunks_by_id]}
        for relation in relationships:
            relation["source_references"] = make_references(relation.get("source_block_ids", []), relation.get("chunk_ids", []))
            relation["provenance"] = {"document_id": str(doc_id), "investigation_id": str(investigation_id), "run_ids": [chunks_by_id[cid].get("run_id") for cid in relation.get("chunk_ids", []) if cid in chunks_by_id]}

        current.processing_started_at = datetime.now(timezone.utc)
        payload = {
            "schema_version": "relationship_extraction_v1", "model": model,
            "pipeline_version": EXTRACTION_PIPELINE_VERSION,
            "status": "partial", "entities": entities, "relationships": relationships,
            "chunks": chunks,
            "run": {"run_id": str(run_id), "source_sha256": source_sha256, "model": model, "status": "partial"},
        }
        if isinstance(deterministic, dict):
            payload["deterministic_analysis"] = deterministic
            payload["analysis_stages"] = {
                "source_extraction": "completed",
                "deterministic_evidence": deterministic.get("status", "completed"),
                "investigation_correlation": deterministic.get("correlation_status", "completed"),
                "ai_enrichment": "processing", "investigator_review": "pending",
            }
        if record:
            record.model = model
            record.payload = payload
        else:
            db.add(RelationshipAnalysis(document_id=doc_id, model=model, payload=payload))
        await append_audit(
            db, investigation_id=investigation_id, actor_user_id=None,
            action="relationship_extraction.chunk_completed", target_type="relationship_extraction_run",
            target_id=str(run_id), details={
                "document_id": str(doc_id), "chunk_id": chunk["chunk_id"],
                "chunk_number": chunk["chunk_number"], "duration_ms": chunk["duration_ms"],
                "llm_request_ms": chunk.get("llm_request_ms", 0),
                "attempts": chunk["attempts"], "entity_count": len(chunk["entities"]),
                "relationship_count": len(chunk["relationships"]), "source_block_count": len(chunk["input_block_ids"]),
            },
        )
        await db.commit()


async def _run_relationship_extraction(doc_id) -> None:
    from app.services.relationship_extraction import (
        EXTRACTION_PIPELINE_VERSION, RelationshipExtractionError, extract, relationship_cache_key,
    )

    worker_started = time.perf_counter()
    async with SessionLocal() as db:
        current = await db.scalar(select(Document).where(Document.id == doc_id).with_for_update())
        result = await db.scalar(select(OCRResult).where(OCRResult.document_id == doc_id))
        if current is None or current.processing_status != "relationship_extraction_pending":
            return
        investigation_id = current.investigation_id
        source_sha256 = current.sha256
        document_id = current.id
        blocks = _relationship_blocks(result.blocks if result and isinstance(result.blocks, list) else [])
        block_by_id = {str(block.get("block_id")): block for block in blocks if block.get("block_id")}
        record = await db.scalar(select(RelationshipAnalysis).where(RelationshipAnalysis.document_id == doc_id))
        previous_payload = record.payload if record and isinstance(record.payload, dict) else {}
        previous_run = previous_payload.get("run") if isinstance(previous_payload.get("run"), dict) else {}
        cache_key = relationship_cache_key(blocks, model=settings.llm_model)
        if (
            previous_run.get("status") == "completed"
            and previous_run.get("source_sha256") == source_sha256
            and previous_run.get("model") == settings.llm_model
            and previous_run.get("pipeline_version") == EXTRACTION_PIPELINE_VERSION
            and previous_run.get("cache_key") == cache_key
        ):
            completed_at = datetime.now(timezone.utc)
            current.processing_status = "relationship_extraction_completed"
            current.processing_error = None
            current.processing_error_code = None
            current.processing_error_retryable = None
            current.processing_completed_at = completed_at
            await append_audit(
                db, investigation_id=investigation_id, actor_user_id=None,
                action="relationship_extraction.cache_hit", target_type="document",
                target_id=str(document_id), details={
                    "source_sha256": source_sha256, "model": settings.llm_model,
                    "pipeline_version": EXTRACTION_PIPELINE_VERSION,
                    "duration_ms": round((time.perf_counter() - worker_started) * 1000, 3),
                },
            )
            await db.commit()
            print(
                f"Relationship extraction cache hit for document {document_id} "
                f"(duration_ms={round((time.perf_counter() - worker_started) * 1000, 2)}, provider_calls=0)",
                flush=True,
            )
            return
        if current.relationship_extraction_attempts >= 3:
            current.processing_status = "relationship_extraction_failed"
            current.processing_error = "Relationship extraction reached the retry limit."
            current.processing_error_code = "retry_limit_exceeded"
            current.processing_error_retryable = False
            current.processing_completed_at = datetime.now(timezone.utc)
            await db.commit()
            return
        current.relationship_extraction_attempts += 1
        current.processing_status = "relationship_extraction_processing"
        current.processing_started_at = datetime.now(timezone.utc)
        current.processing_error = None
        current.processing_error_code = None
        current.processing_error_retryable = None
        attempt = current.relationship_extraction_attempts
        resume_chunks = previous_payload.get("chunks", []) if (
            previous_run.get("source_sha256") == source_sha256
            and previous_payload.get("model") == settings.llm_model
            and previous_payload.get("pipeline_version") == EXTRACTION_PIPELINE_VERSION
            and previous_run.get("status") == "partial"
            and isinstance(previous_payload.get("chunks"), list)
        ) else []
        input_characters = sum(len(str(block.get("text") or "")) for block in blocks)
        run_id = uuid.uuid4()
        started_at = datetime.now(timezone.utc)
        await append_audit(
            db,
            investigation_id=investigation_id,
            actor_user_id=None,
            action="relationship_extraction.started",
            target_type="relationship_extraction_run",
            target_id=str(run_id),
            details={
                "document_id": str(document_id), "source_sha256": source_sha256,
                "model": settings.llm_model, "input_block_count": len(blocks),
                "input_characters": input_characters,
                "attempt": attempt,
            },
        )
        await db.commit()

    try:
        deterministic = await _persist_deterministic_evidence(doc_id)
        async with SessionLocal() as db:
            record = await db.scalar(select(RelationshipAnalysis).where(RelationshipAnalysis.document_id == doc_id))
            if record and isinstance(record.payload, dict):
                payload = dict(record.payload)
                stages = dict(payload.get("analysis_stages") or {})
                stages["ai_enrichment"] = "processing"
                payload["analysis_stages"] = stages
                record.payload = payload
                await db.commit()
        if blocks:
            loop = asyncio.get_running_loop()
            checkpoint_persistence_ms = []

            def persist_chunk(chunk):
                persistence_started = time.perf_counter()
                future = asyncio.run_coroutine_threadsafe(
                    _persist_relationship_chunk(
                        doc_id, investigation_id, source_sha256, settings.llm_model,
                        run_id, chunk, block_by_id,
                    ),
                    loop,
                )
                try:
                    future.result()
                except Exception as exc:
                    raise _RelationshipPersistenceError(type(exc).__name__) from None
                checkpoint_persistence_ms.append(round((time.perf_counter() - persistence_started) * 1000, 3))

            graph, metadata = await asyncio.to_thread(
                extract, blocks,
                completed_chunks=resume_chunks,
                on_chunk_completed=persist_chunk,
            )
        else:
            checkpoint_persistence_ms = []
            graph, metadata = (
                {"schema_version": "relationship_extraction_v1", "entities": [], "relationships": []},
                {"usage": [], "chunk_count": 0, "input_block_count": 0, "input_characters": 0, "attempts": 0, "duration_ms": 0},
            )
        completed_at = datetime.now(timezone.utc)
        elapsed_ms = round((completed_at - started_at).total_seconds() * 1000, 2)
        graph["usage"] = metadata.get("usage", [])
        graph["pipeline_version"] = EXTRACTION_PIPELINE_VERSION
        stage_timings = dict(metadata.get("stage_timings_ms", {}))
        stage_timings["database_checkpoint_persistence_ms"] = round(sum(checkpoint_persistence_ms), 3)
        chunk_run_ids = {item["chunk_id"]: item.get("run_id", str(run_id)) for item in graph.get("chunks", [])}
        chunks_by_id = {item["chunk_id"]: item for item in graph.get("chunks", [])}

        def source_references(block_ids: list[str], chunk_ids: list[str]) -> list[dict]:
            refs = []
            for block_id in block_ids:
                block = block_by_id.get(str(block_id))
                if not block:
                    continue
                matching = [chunks_by_id[cid] for cid in chunk_ids if cid in chunks_by_id and block_id in chunks_by_id[cid].get("input_block_ids", [])]
                for chunk in matching or [None]:
                    page_index = block.get("page_index")
                    refs.append({
                        "document_id": str(doc_id), "investigation_id": str(investigation_id),
                        "run_id": chunk.get("run_id", str(run_id)) if chunk else str(run_id),
                        "source_sha256": source_sha256, "block_id": str(block_id),
                        "page_index": page_index,
                        "page_number": page_index + 1 if isinstance(page_index, int) and not isinstance(page_index, bool) else None,
                        "extraction_method": block.get("extraction_method", "ocr"),
                        "coordinate_space": block.get("bbox_space"),
                        "bounding_box": block.get("engine_bbox"), "quoted_text": block.get("text"),
                    })
            return refs

        for entity in graph.get("entities", []):
            entity["source_references"] = source_references(entity.get("source_block_ids", []), entity.get("chunk_ids", []))
            entity["provenance"] = {"document_id": str(doc_id), "investigation_id": str(investigation_id), "run_ids": [chunk_run_ids[cid] for cid in entity.get("chunk_ids", []) if cid in chunk_run_ids]}
        for relation in graph.get("relationships", []):
            relation["source_references"] = source_references(relation.get("source_block_ids", []), relation.get("chunk_ids", []))
            relation["provenance"] = {"document_id": str(doc_id), "investigation_id": str(investigation_id), "run_ids": [chunk_run_ids[cid] for cid in relation.get("chunk_ids", []) if cid in chunk_run_ids]}

        graph["run"] = {
            "run_id": str(run_id), "document_id": str(doc_id),
            "investigation_id": str(investigation_id),
            "source_sha256": source_sha256, "model": settings.llm_model,
            "provider": settings.llm_provider, "schema_version": graph.get("schema_version"),
            "pipeline_version": EXTRACTION_PIPELINE_VERSION,
            "cache_key": metadata.get("cache_key"),
            "started_at": started_at.isoformat(), "completed_at": completed_at.isoformat(),
            "duration_ms": metadata.get("duration_ms", elapsed_ms),
            "input_block_count": len(blocks), "input_characters": input_characters,
            "chunk_count": metadata.get("chunk_count", 1 if blocks else 0),
            "attempts": metadata.get("attempts", 1 if blocks else 0),
            "stage_timings_ms": stage_timings,
            "status": "completed" if blocks else "skipped_empty_input",
        }
        graph["deterministic_analysis"] = deterministic
        graph["analysis_stages"] = {
            "source_extraction": "completed",
            "deterministic_evidence": deterministic["status"],
            "investigation_correlation": deterministic["correlation_status"],
            "ai_enrichment": "completed", "investigator_review": "pending",
        }

        graph["chunks"] = [{key: value for key, value in chunk.items() if key not in {"entities", "relationships"}} for chunk in graph.get("chunks", [])]

        async with SessionLocal() as db:
            current = await db.scalar(select(Document).where(Document.id == doc_id))
            if current is None:
                return
            record = await db.scalar(select(RelationshipAnalysis).where(RelationshipAnalysis.document_id == doc_id))
            if record:
                record.model = settings.llm_model
                record.payload = graph
            else:
                db.add(RelationshipAnalysis(document_id=doc_id, model=settings.llm_model, payload=graph))
            current.processing_status = "relationship_extraction_completed"
            current.processing_error = None
            current.processing_error_code = None
            current.processing_error_retryable = None
            current.processing_completed_at = completed_at
            await append_audit(
                db,
                investigation_id=current.investigation_id,
                actor_user_id=None,
                action="relationship_extraction.completed",
                target_type="document",
                target_id=str(current.id),
                details={
                    "run_id": str(run_id), "model": settings.llm_model,
                    "duration_ms": graph["run"]["duration_ms"],
                    "input_block_count": len(blocks), "input_characters": input_characters,
                    "chunk_count": graph["run"]["chunk_count"], "attempts": graph["run"]["attempts"],
                    "entity_count": len(graph.get("entities", [])),
                    "relationship_count": len(graph.get("relationships", [])),
                    "stage_timings_ms": stage_timings,
                },
            )
            await db.commit()
        print(
            f"Relationship extraction completed for document {doc_id} "
            f"(duration_ms={graph['run']['duration_ms']}, entities={len(graph.get('entities', []))}, "
            f"relationships={len(graph.get('relationships', []))}, chunks={graph['run']['chunk_count']}, "
            f"stage_timings_ms={json.dumps(stage_timings, sort_keys=True)})",
            flush=True,
        )
    except RelationshipExtractionError as exc:
        await _persist_relationship_failure(
            doc_id, run_id, exc, started_at=started_at,
            input_blocks=len(blocks), input_characters=input_characters,
        )
    except _RelationshipPersistenceError as exc:
        error = RelationshipExtractionError(
            "relationship_persistence_failed", "Relationship chunk results could not be persisted; retry is safe.",
            retryable=True, diagnostics={"exception_type": exc.exception_type},
        )
        await _persist_relationship_failure(
            doc_id, run_id, error, started_at=started_at,
            input_blocks=len(blocks), input_characters=input_characters,
        )
    except Exception as exc:
        error = RelationshipExtractionError(
            "relationship_internal_error", "Relationship extraction failed unexpectedly.",
            retryable=False, diagnostics={"exception_type": type(exc).__name__},
        )
        await _persist_relationship_failure(
            doc_id, run_id, error, started_at=started_at,
            input_blocks=len(blocks), input_characters=input_characters,
        )


async def process_one() -> bool:
    async with SessionLocal() as db:
        expired_at = datetime.now(timezone.utc) - timedelta(seconds=STALE_JOB_TIMEOUT_SECONDS)
        stale = (await db.scalars(select(Document).where(
            Document.processing_status.in_(["processing", "relationship_extraction_processing"]),
            Document.processing_started_at < expired_at,
        ).with_for_update(skip_locked=True))).all()
        for item in stale:
            completed_at = datetime.now(timezone.utc)
            if item.processing_status == "relationship_extraction_processing":
                item.processing_status = "relationship_extraction_failed"
                item.processing_error = "Relationship extraction worker stopped before completion."
                item.processing_error_code = "relationship_worker_interrupted"
                item.processing_error_retryable = True
                action = "relationship_extraction.failed"
            else:
                item.processing_status = "failed"
                item.processing_error = "OCR worker stopped before completion."
                item.processing_error_code = "ocr_worker_interrupted"
                item.processing_error_retryable = True
                action = "extraction.failed"
            item.processing_completed_at = completed_at
            await append_audit(
                db,
                investigation_id=item.investigation_id,
                actor_user_id=None,
                action=action,
                target_type="document",
                target_id=str(item.id),
                details={"error_code": item.processing_error_code, "retryable": True},
            )
        if stale:
            await db.commit()
        relation_doc = await db.scalar(select(Document).where(
            Document.processing_status == "relationship_extraction_pending",
        ).order_by(Document.processing_started_at, Document.created_at).with_for_update(skip_locked=True).limit(1))
        relation_doc_id = relation_doc.id if relation_doc is not None else None
        if relation_doc_id is None:
            doc = await db.scalar(select(Document).where(Document.processing_status == "queued").order_by(Document.created_at).with_for_update(skip_locked=True).limit(1))
            if doc is None:
                return False
            if doc.processing_attempts >= 3:
                doc.processing_status = "failed"
                doc.processing_error = "Document processing reached the retry limit."
                doc.processing_error_code = "retry_limit_exceeded"
                doc.processing_error_retryable = False
                doc.processing_completed_at = datetime.now(timezone.utc)
                await db.commit()
                return True
            doc.processing_attempts += 1
            doc.processing_status = "processing"
            doc.processing_started_at = datetime.now(timezone.utc)
            doc.processing_error = None
            doc.processing_error_code = None
            doc.processing_error_retryable = None
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
        else:
            doc_id = relation_doc_id
            extraction_run_id = None
    if extraction_run_id is None:
        await _run_relationship_extraction(doc_id)
        return True
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
        native_blocks = _native_pdf_blocks(path)
        source_blocks.extend(native_blocks)
        engine_raw = raw
        page_metadata = engine_raw.get("page_metadata", []) if isinstance(engine_raw, dict) else []
        page_count = len(page_metadata) if isinstance(page_metadata, list) else 0
        if _is_pdf_file(path):
            with fitz.open(path) as source_pdf:
                page_count = source_pdf.page_count
        elif not page_count:
            page_count = max((block.get("page_index", -1) for block in source_blocks), default=-1) + 1
        raw = {
            "engine_output": engine_raw,
            "page_metadata": page_metadata,
            "native_pdf_text": native_blocks,
            "extraction_coverage": {
                "page_count": page_count,
                "ocr_blocks": sum(block.get("extraction_method") == "ocr" for block in source_blocks),
                "native_pdf_blocks": len(native_blocks),
                "pages_with_native_text": len({block["page_index"] for block in native_blocks}),
                "pages_with_ocr_text": len({block.get("page_index", 0) for block in source_blocks if block.get("extraction_method") == "ocr"}),
            },
        }
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
            for block in source_blocks:
                page_index = block.get("page_index")
                if not isinstance(page_index, int) or isinstance(page_index, bool) or page_index < 0:
                    page_index = 0
                metadata = page_metadata.get(page_index, {})
                geometry = metadata.get("geometry") if isinstance(metadata, dict) else None
                if isinstance(geometry, dict) and isinstance(block.get("engine_bbox"), (list, dict)):
                    block["bbox_space"] = metadata.get("bbox_space")
                    block["page_geometry"] = geometry
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
                    text=_logical_text(blocks),
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
                    extraction_method=block.get("extraction_method", "ocr"),
                    coordinate_space=block.get("bbox_space") or ("ocr_pixels" if block.get("engine_bbox") is not None else None),
                    source_sha256=current.sha256,
                    source_reference=str(block.get("source_path") or block["block_id"]),
                ))
            record = await db.scalar(select(OCRResult).where(OCRResult.document_id == doc_id))
            fields = dict(provider="paddleocr_ppstructurev3", provider_version=provider, raw_result=raw, blocks=source_blocks, recognized_text=_logical_text(source_blocks))
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
            current.processing_status = "relationship_extraction_pending" if settings.relationship_extraction_enabled else "ocr_completed"
            current.processing_error = None
            current.processing_error_code = None
            current.processing_error_retryable = None
            if settings.relationship_extraction_enabled:
                current.processing_started_at = datetime.now(timezone.utc)
                current.processing_completed_at = None
            else:
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

    if settings.relationship_extraction_enabled:
        await _run_relationship_extraction(doc_id)
    else:
        try:
            await _persist_deterministic_evidence(doc_id)
        except Exception as exc:
            print(
                f"Deterministic relationship analysis failed for document {doc_id} "
                f"(exception_type={type(exc).__name__})",
                flush=True,
            )
    return True

import secrets
import hashlib
import os
import re
import uuid as uuid_module
from pathlib import Path
from urllib.parse import unquote
from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import FileResponse, Response
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_db, require_csrf, require_permission
from app.core.config import settings
from app.models.entities import ActivityEvent, DerivedGraphNode, Document, EvidencePage, EvidenceSpan, ExtractionRun, Finding, Investigation, InvestigationStatus, OCRResult, RelationshipAnalysis, TimelineEvent, User
from app.services.audit_chain import append_audit
from app.schemas.investigation import DocumentResponse, InvestigationCreate, InvestigationPatch

router = APIRouter()
ALLOWED_UPLOADS = {
    ".jpg": ("image/jpeg", b"\xff\xd8\xff"),
    ".jpeg": ("image/jpeg", b"\xff\xd8\xff"),
    ".png": ("image/png", b"\x89PNG\r\n\x1a\n"),
    ".tif": ("image/tiff", b"II*\x00"),
    ".tiff": ("image/tiff", b"II*\x00"),
    ".pdf": ("application/pdf", b"%PDF-"),
}
MAX_UPLOAD_BYTES = 25 * 1024 * 1024
STORAGE_KEY_PATTERN = re.compile(r"^[0-9a-f]{32}$")


def stored_document_path(doc: Document, *, require_exists: bool = True) -> Path:
    if not doc.storage_key or not STORAGE_KEY_PATTERN.fullmatch(doc.storage_key):
        raise HTTPException(status_code=404, detail="Stored document is unavailable")
    root = Path(settings.document_storage_path).resolve()
    path = (root / doc.storage_key).resolve()
    if path.parent != root or path.is_symlink() or (require_exists and not path.is_file()):
        raise HTTPException(status_code=404, detail="Stored document is unavailable")
    return path


async def owned_investigation(db: AsyncSession, user: User, investigation_id: UUID) -> Investigation:
    investigation = await db.scalar(select(Investigation).where(
        Investigation.id == investigation_id,
        Investigation.created_by_user_id == user.id,
    ))
    if investigation is None:
        raise HTTPException(status_code=404, detail="Investigation not found")
    return investigation


async def as_response(db: AsyncSession, item: Investigation, document_count: int | None = None) -> dict:
    count = document_count
    if count is None:
        count = await db.scalar(select(func.count(Document.id)).where(Document.investigation_id == item.id))
    return {
        "id": str(item.id), "case_number": item.case_number, "title": item.title,
        "description": item.description, "status": item.status.value,
        "document_count": count or 0, "created_at": item.created_at, "updated_at": item.updated_at,
    }


@router.get("")
async def list_investigations(
    status_filter: InvestigationStatus | None = Query(default=None, alias="status"),
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=20, ge=1, le=100),
    sort_by: Literal["updated_at", "created_at", "case_number", "title"] = "updated_at",
    order: Literal["asc", "desc"] = "desc",
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_permission("investigations:read")),
):
    query = select(Investigation).where(Investigation.created_by_user_id == user.id)
    if status_filter is not None:
        query = query.where(Investigation.status == status_filter)
    total = await db.scalar(select(func.count(Investigation.id)).where(
        Investigation.created_by_user_id == user.id,
        *([Investigation.status == status_filter] if status_filter is not None else []),
    ))
    sort_column = getattr(Investigation, sort_by)
    ordering = sort_column.asc() if order == "asc" else sort_column.desc()
    document_count = (
        select(func.count(Document.id))
        .where(Document.investigation_id == Investigation.id)
        .correlate(Investigation)
        .scalar_subquery()
    )
    rows = (await db.execute(
        query.with_only_columns(Investigation, document_count)
        .order_by(ordering)
        .offset(offset)
        .limit(limit)
    )).all()
    return {
        "items": [await as_response(db, item, count) for item, count in rows],
        "offset": offset,
        "limit": limit,
        "total": total or 0,
    }


@router.post("", status_code=201)
async def create_investigation(payload: InvestigationCreate, request: Request, db: AsyncSession = Depends(get_db), user: User = Depends(require_permission("investigations:create"))):
    require_csrf(request)
    item = Investigation(
        case_number=f"DF-{secrets.token_hex(6).upper()}",
        title=payload.title.strip(),
        description=payload.description.strip() if payload.description else None,
        status=InvestigationStatus.draft,
        created_by_user_id=user.id,
    )
    db.add(item)
    await db.flush()
    db.add(ActivityEvent(actor_user_id=user.id, investigation_id=item.id, event_type="investigation.created", summary="Created investigation"))
    await append_audit(
        db,
        investigation_id=item.id,
        actor_user_id=user.id,
        action="investigation.created",
        target_type="investigation",
        target_id=str(item.id),
        details={"case_number": item.case_number},
    )
    await db.commit()
    await db.refresh(item)
    return await as_response(db, item)


@router.get("/{investigation_id}")
async def get_investigation(investigation_id: UUID, db: AsyncSession = Depends(get_db), user: User = Depends(require_permission("investigations:read"))):
    item = await owned_investigation(db, user, investigation_id)
    return await as_response(db, item)


@router.patch("/{investigation_id}")
async def update_investigation(investigation_id: UUID, payload: InvestigationPatch, request: Request, db: AsyncSession = Depends(get_db), user: User = Depends(require_permission("investigations:update"))):
    require_csrf(request)
    item = await owned_investigation(db, user, investigation_id)
    changes = payload.model_dump(exclude_unset=True)
    if not changes:
        raise HTTPException(status_code=422, detail="At least one field must be provided")
    if "title" in changes and (changes["title"] is None or not changes["title"].strip()):
        raise HTTPException(status_code=422, detail="Title cannot be empty")
    if "status" in changes and changes["status"] is None:
        raise HTTPException(status_code=422, detail="Status cannot be empty")
    for key, value in changes.items():
        setattr(item, key, value.strip() if isinstance(value, str) else value)
    db.add(ActivityEvent(actor_user_id=user.id, investigation_id=item.id, event_type="investigation.updated", summary="Updated investigation"))
    await db.commit()
    await db.refresh(item)
    return await as_response(db, item)


@router.get("/{investigation_id}/documents", response_model=list[DocumentResponse])
async def list_documents(investigation_id: UUID, db: AsyncSession = Depends(get_db), user: User = Depends(require_permission("investigations:read"))):
    await owned_investigation(db, user, investigation_id)
    documents = (await db.scalars(select(Document).where(Document.investigation_id == investigation_id).order_by(Document.created_at.desc()))).all()
    return [{
        "id": str(doc.id), "filename": doc.filename, "content_type": doc.content_type,
        "source_description": doc.source_description,
        "uploaded_by_user_id": str(doc.uploaded_by_user_id) if doc.uploaded_by_user_id else None,
        "size_bytes": doc.size_bytes, "processing_status": doc.processing_status,
        "attempts_remaining": max(0, 3 - doc.processing_attempts), "created_at": doc.created_at,
        "sha256": doc.sha256,
    } for doc in documents]


@router.post("/{investigation_id}/documents", status_code=201)
async def upload_document(investigation_id: UUID, request: Request, db: AsyncSession = Depends(get_db), user: User = Depends(require_permission("investigations:update"))):
    require_csrf(request)
    investigation = await owned_investigation(db, user, investigation_id)
    original = Path(unquote(request.headers.get("x-file-name", ""))).name
    suffix = Path(original).suffix.lower()
    expected = ALLOWED_UPLOADS.get(suffix)
    source_description = request.headers.get("x-source-description")
    if source_description is not None and len(source_description) > 2000:
        raise HTTPException(status_code=422, detail="Source description must be 2000 characters or fewer")
    if not original or len(original) > 255 or not expected or request.headers.get("content-type", "").split(";")[0].strip().lower() != expected[0]:
        raise HTTPException(status_code=415, detail="Supported uploads are JPEG, PNG, TIFF images, and PDF documents")
    storage_key = uuid_module.uuid4().hex
    storage_root = Path(settings.document_storage_path).resolve()
    storage_root.mkdir(parents=True, exist_ok=True)
    target = storage_root / storage_key
    digest = hashlib.sha256()
    size = 0
    prefix = b""
    suffix_bytes = b""
    try:
        with target.open("xb") as output:
            async for chunk in request.stream():
                if not chunk:
                    continue
                size += len(chunk)
                if size > MAX_UPLOAD_BYTES:
                    raise HTTPException(status_code=413, detail="File exceeds the 25 MiB upload limit")
                if len(prefix) < 8:
                    prefix += chunk[:8 - len(prefix)]
                suffix_bytes = (suffix_bytes + chunk)[-16:]
                digest.update(chunk)
                output.write(chunk)
        valid_tiff = suffix in {".tif", ".tiff"} and prefix.startswith((b"II*\x00", b"MM\x00*"))
        valid_pdf = suffix == ".pdf" and prefix.startswith(b"%PDF-") and suffix_bytes.rstrip().endswith(b"%%EOF")
        valid_content = valid_pdf if suffix == ".pdf" else prefix.startswith(expected[1]) or valid_tiff
        if not valid_content:
            raise HTTPException(status_code=415, detail="File content does not match its declared image type")
        clean_name = re.sub(r"[^\w.() -]", "_", original, flags=re.UNICODE)[:255] or "document" + suffix
        doc = Document(investigation_id=investigation.id, uploaded_by_user_id=user.id,
                       source_description=source_description.strip() if source_description and source_description.strip() else None,
                       filename=clean_name, content_type=expected[0], size_bytes=size,
                       storage_key=storage_key, sha256=digest.hexdigest(), processing_status="queued")
        db.add(doc)
        await db.flush()
        await append_audit(
            db,
            investigation_id=investigation.id,
            actor_user_id=user.id,
            action="evidence.uploaded",
            target_type="document",
            target_id=str(doc.id),
            details={"filename": doc.filename, "sha256": doc.sha256, "size_bytes": size, "content_type": doc.content_type, "source_description": doc.source_description},
        )
        await db.commit()
        await db.refresh(doc)
        return {"id": str(doc.id), "filename": doc.filename, "content_type": doc.content_type, "size_bytes": size,
                "source_description": doc.source_description, "uploaded_by_user_id": str(user.id),
                "processing_status": doc.processing_status, "created_at": doc.created_at, "sha256": doc.sha256}
    except Exception:
        target.unlink(missing_ok=True)
        raise
@router.get("/{investigation_id}/documents/{document_id}/status")
async def document_status(investigation_id: UUID, document_id: UUID, db: AsyncSession = Depends(get_db), user: User = Depends(require_permission("investigations:read"))):
    await owned_investigation(db, user, investigation_id)
    doc = await db.scalar(select(Document).where(Document.id == document_id, Document.investigation_id == investigation_id))
    if doc is None:
        raise HTTPException(status_code=404, detail="Document not found")
    failed = doc.processing_status in {"failed", "relationship_extraction_failed"}
    return {"id": str(doc.id), "status": doc.processing_status, "error": doc.processing_error if failed else None}


@router.get("/{investigation_id}/documents/{document_id}")
async def document_details(investigation_id: UUID, document_id: UUID, db: AsyncSession = Depends(get_db), user: User = Depends(require_permission("investigations:read"))):
    await owned_investigation(db, user, investigation_id)
    doc = await db.scalar(select(Document).where(Document.id == document_id, Document.investigation_id == investigation_id))
    if doc is None:
        raise HTTPException(status_code=404, detail="Document not found")
    latest_run = await db.scalar(
        select(ExtractionRun).where(ExtractionRun.document_id == doc.id).order_by(ExtractionRun.run_number.desc()).limit(1)
    )
    return {
        "id": str(doc.id),
        "investigation_id": str(doc.investigation_id),
        "filename": doc.filename,
        "source_description": doc.source_description,
        "uploaded_by_user_id": str(doc.uploaded_by_user_id) if doc.uploaded_by_user_id else None,
        "content_type": doc.content_type,
        "size_bytes": doc.size_bytes,
        "sha256": doc.sha256,
        "processing_status": doc.processing_status,
        "processing_error": doc.processing_error,
        "processing_attempts": doc.processing_attempts,
        "created_at": doc.created_at.isoformat(),
        "processing_started_at": doc.processing_started_at.isoformat() if doc.processing_started_at else None,
        "processing_completed_at": doc.processing_completed_at.isoformat() if doc.processing_completed_at else None,
        "latest_extraction_run_id": str(latest_run.id) if latest_run else None,
        "file_url": f"/investigations/{investigation_id}/documents/{document_id}/file",
    }


@router.get("/{investigation_id}/documents/{document_id}/file")
async def get_document_file(investigation_id: UUID, document_id: UUID, db: AsyncSession = Depends(get_db), user: User = Depends(require_permission("investigations:read"))):
    await owned_investigation(db, user, investigation_id)
    doc = await db.scalar(select(Document).where(Document.id == document_id, Document.investigation_id == investigation_id))
    if doc is None:
        raise HTTPException(status_code=404, detail="Document not found")
    path = stored_document_path(doc)
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    computed_hash = digest.hexdigest()
    if computed_hash != doc.sha256:
        raise HTTPException(status_code=409, detail="Stored evidence hash does not match its recorded SHA-256")
    await append_audit(
        db,
        investigation_id=investigation_id,
        actor_user_id=user.id,
        action="evidence.accessed",
        target_type="document",
        target_id=str(doc.id),
        details={"sha256": computed_hash},
    )
    await db.commit()
    return FileResponse(path, media_type=doc.content_type, filename=doc.filename, content_disposition_type="inline",
                        headers={"X-Content-SHA256": computed_hash, "Cache-Control": "no-store"})


@router.get("/{investigation_id}/documents/{document_id}/pages/{page_number}/preview")
async def get_document_page_preview(investigation_id: UUID, document_id: UUID, page_number: int, db: AsyncSession = Depends(get_db), user: User = Depends(require_permission("investigations:read"))):
    await owned_investigation(db, user, investigation_id)
    doc = await db.scalar(select(Document).where(Document.id == document_id, Document.investigation_id == investigation_id))
    if doc is None or page_number < 1:
        raise HTTPException(status_code=404, detail="Document page not found")
    if doc.content_type != "application/pdf":
        raise HTTPException(status_code=415, detail="Page previews are available for PDF sources")
    path = stored_document_path(doc)
    try:
        import fitz
        pdf = fitz.open(path)
        try:
            if page_number > pdf.page_count:
                raise HTTPException(status_code=404, detail="Document page not found")
            pix = pdf[page_number - 1].get_pixmap(matrix=fitz.Matrix(1.5, 1.5), alpha=False)
            image_bytes = pix.tobytes("png")
        finally:
            pdf.close()
    except HTTPException:
        raise
    except ImportError as exc:
        raise HTTPException(status_code=503, detail="PDF page preview requires PyMuPDF") from exc
    except Exception as exc:
        raise HTTPException(status_code=422, detail="The requested PDF page could not be rendered") from exc
    await append_audit(db, investigation_id=investigation_id, actor_user_id=user.id, action="evidence.page_previewed", target_type="document", target_id=str(doc.id), details={"page_number": page_number, "sha256": doc.sha256})
    await db.commit()
    return Response(image_bytes, media_type="image/png", headers={"Cache-Control": "no-store", "X-Source-Page": str(page_number)})


@router.get("/{investigation_id}/documents/{document_id}/extraction-runs")
async def list_extraction_runs(investigation_id: UUID, document_id: UUID, db: AsyncSession = Depends(get_db), user: User = Depends(require_permission("investigations:read"))):
    await owned_investigation(db, user, investigation_id)
    doc = await db.scalar(select(Document).where(Document.id == document_id, Document.investigation_id == investigation_id))
    if doc is None:
        raise HTTPException(status_code=404, detail="Document not found")
    runs = (await db.scalars(
        select(ExtractionRun).where(ExtractionRun.document_id == doc.id).order_by(ExtractionRun.run_number.desc())
    )).all()
    return {"items": [{
        "id": str(run.id),
        "run_number": run.run_number,
        "provider": run.provider,
        "provider_version": run.provider_version,
        "configuration": run.configuration,
        "status": run.status,
        "error_detail": run.error_detail,
        "latency_ms": run.latency_ms,
        "started_at": run.started_at.isoformat() if run.started_at else None,
        "completed_at": run.completed_at.isoformat() if run.completed_at else None,
        "created_at": run.created_at.isoformat(),
    } for run in runs]}


@router.get("/{investigation_id}/documents/{document_id}/extraction-runs/{run_id}")
async def get_extraction_run(investigation_id: UUID, document_id: UUID, run_id: UUID, db: AsyncSession = Depends(get_db), user: User = Depends(require_permission("investigations:read"))):
    await owned_investigation(db, user, investigation_id)
    run = await db.scalar(
        select(ExtractionRun)
        .join(Document, Document.id == ExtractionRun.document_id)
        .where(ExtractionRun.id == run_id, ExtractionRun.document_id == document_id, Document.investigation_id == investigation_id)
    )
    if run is None:
        raise HTTPException(status_code=404, detail="Extraction run not found")
    pages = (await db.scalars(
        select(EvidencePage).where(EvidencePage.extraction_run_id == run.id).order_by(EvidencePage.page_number)
    )).all()
    spans = (await db.scalars(
        select(EvidenceSpan).where(EvidenceSpan.extraction_run_id == run.id).order_by(EvidenceSpan.page_id, EvidenceSpan.id)
    )).all()
    return {
        "id": str(run.id),
        "run_number": run.run_number,
        "provider": run.provider,
        "provider_version": run.provider_version,
        "configuration": run.configuration,
        "status": run.status,
        "error_detail": run.error_detail,
        "latency_ms": run.latency_ms,
        "pages": [{"id": str(page.id), "page_number": page.page_number, "width": page.width, "height": page.height, "text": page.text} for page in pages],
        "spans": [{
            "id": str(span.id),
            "page_id": str(span.page_id) if span.page_id else None,
            "block_id": span.block_id,
            "text": span.text,
            "bounding_box": span.bounding_box,
            "confidence": span.confidence,
            "source_reference": span.source_reference,
        } for span in spans],
    }


@router.get("/{investigation_id}/documents/{document_id}/ocr")
async def document_ocr(investigation_id: UUID, document_id: UUID, include_raw: bool = False, db: AsyncSession = Depends(get_db), user: User = Depends(require_permission("investigations:read"))):
    await owned_investigation(db, user, investigation_id)
    doc = await db.scalar(select(Document).where(Document.id == document_id, Document.investigation_id == investigation_id))
    if doc is None:
        raise HTTPException(status_code=404, detail="Document not found")
    result = await db.scalar(select(OCRResult).where(OCRResult.document_id == document_id))
    if result is None:
        raise HTTPException(status_code=409, detail="OCR output is not available yet")
    data = {"document_id": str(document_id), "provider": result.provider, "provider_version": result.provider_version,
            "recognized_text": result.recognized_text, "blocks": result.blocks}
    if include_raw:
        data["raw_result"] = result.raw_result
    return data


@router.post("/{investigation_id}/documents/{document_id}/retry", status_code=202)
async def retry_document(investigation_id: UUID, document_id: UUID, request: Request, db: AsyncSession = Depends(get_db), user: User = Depends(require_permission("investigations:update"))):
    require_csrf(request)
    await owned_investigation(db, user, investigation_id)
    doc = await db.scalar(select(Document).where(Document.id == document_id, Document.investigation_id == investigation_id))
    if doc is None:
        raise HTTPException(status_code=404, detail="Document not found")
    if doc.processing_status not in {"failed", "relationship_extraction_failed"} or doc.processing_attempts >= 3:
        raise HTTPException(status_code=409, detail="This document cannot be retried")
    doc.processing_status = "queued"
    doc.processing_error = None
    doc.processing_started_at = None
    doc.processing_completed_at = None
    await db.commit()
    return {"id": str(document_id), "status": "queued", "attempts_remaining": 3 - doc.processing_attempts}


@router.get("/{investigation_id}/relationships/graph")
async def relationship_graph(investigation_id: UUID, db: AsyncSession = Depends(get_db), user: User = Depends(require_permission("investigations:read"))):
    await owned_investigation(db, user, investigation_id)
    documents = (await db.scalars(
        select(Document).where(Document.investigation_id == investigation_id).order_by(Document.created_at, Document.id)
    )).all()
    document_by_id = {str(document.id): document for document in documents}
    graph_nodes: dict[str, dict] = {}
    graph_edges: list[dict] = []
    nodes: dict[str, dict] = {}
    edges: list[dict] = []
    for document in documents:
        graph_id = f"document:{document.id}"
        graph_nodes[graph_id] = {
            "id": graph_id,
            "kind": "document",
            "label": document.filename,
            "type": document.content_type,
            "status": document.processing_status,
            "confidence": None,
            "document_id": str(document.id),
            "metadata": {
                "sha256": document.sha256,
                "size_bytes": document.size_bytes,
                "source_description": document.source_description,
                "uploaded_by_user_id": str(document.uploaded_by_user_id) if document.uploaded_by_user_id else None,
            },
        }

    records = (await db.execute(
        select(RelationshipAnalysis, Document, OCRResult)
        .join(Document, Document.id == RelationshipAnalysis.document_id)
        .outerjoin(OCRResult, OCRResult.document_id == Document.id)
        .where(Document.investigation_id == investigation_id)
    )).all()
    for analysis, document, ocr_result in records:
        blocks = {block.get("block_id"): block for block in (ocr_result.blocks if ocr_result else []) if isinstance(block, dict)}
        for mention in analysis.payload.get("entities", []):
            entity_id = mention["canonical_entity_id"]
            node = nodes.setdefault(entity_id, {"id": entity_id, "label": mention["mention_text"], "type": mention["entity_type"], "document_ids": [], "resolution": mention["resolution"]})
            if str(document.id) not in node["document_ids"]:
                node["document_ids"].append(str(document.id))
            if len(node["document_ids"]) > 1:
                node["resolution"] = "exact_match"
            graph_nodes.setdefault(entity_id, {
                "id": entity_id,
                "kind": "entity",
                "label": mention["mention_text"],
                "type": mention["entity_type"],
                "status": "candidate",
                "confidence": mention.get("confidence"),
                "document_ids": [],
                "resolution": mention["resolution"],
                "metadata": {"source_block_ids": mention["source_block_ids"]},
            })
            if str(document.id) not in graph_nodes[entity_id]["document_ids"]:
                graph_nodes[entity_id]["document_ids"].append(str(document.id))
            mention_refs = [{
                "document_id": str(document.id),
                "filename": document.filename,
                "block_id": block_id,
                "quoted_text": blocks.get(block_id, {}).get("text"),
                "page_number": blocks.get(block_id, {}).get("page_index"),
                "source_sha256": document.sha256,
            } for block_id in mention["source_block_ids"]]
            edge_id = f"mention:{document.id}:{entity_id}"
            graph_edges.append({
                "id": edge_id,
                "source": f"document:{document.id}",
                "target": entity_id,
                "relationship_type": "mentioned_in",
                "label": "mentioned in",
                "type": "MENTIONED_IN",
                "kind": "explicit",
                "confidence": mention.get("confidence"),
                "basis": "Entity mention was extracted from OCR source blocks.",
                "analyzer": "relationship_extraction",
                "status": "candidate",
                "evidence_refs": mention_refs,
            })
        mentions = {m["mention_id"]: m for m in analysis.payload.get("entities", [])}
        for rel in analysis.payload.get("relationships", []):
            subject, obj = mentions[rel["subject_mention_id"]], mentions[rel["object_mention_id"]]
            evidence_refs = [{
                "document_id": str(document.id),
                "filename": document.filename,
                "block_id": block_id,
                "quoted_text": blocks.get(block_id, {}).get("text"),
                "page_number": blocks.get(block_id, {}).get("page_index"),
                "source_sha256": document.sha256,
            } for block_id in rel["source_block_ids"]]
            edge = {
                "id": f"{document.id}_{rel['relationship_id']}",
                "source": subject["canonical_entity_id"],
                "target": obj["canonical_entity_id"],
                "type": rel["predicate"],
                "kind": rel["relationship_kind"],
                "confidence": rel["confidence"],
                "source_document_ids": [str(document.id)],
                "source_block_ids": rel["source_block_ids"],
                "relationship_type": rel["predicate"].lower(),
                "label": rel["predicate"].replace("_", " ").lower(),
                "basis": "Explicitly stated in source blocks." if rel["relationship_kind"] == "explicit" else "Model-inferred from source blocks; requires investigator review.",
                "analyzer": analysis.model,
                "status": "candidate",
                "evidence_refs": evidence_refs,
            }
            edges.append(edge)
            graph_edges.append(edge)
    derived = (await db.scalars(select(DerivedGraphNode).where(DerivedGraphNode.investigation_id == investigation_id))).all()
    for item in derived:
        node = nodes.setdefault(item.canonical_id, {
            "id": item.canonical_id, "label": item.label, "type": item.entity_type,
            "document_ids": [], "resolution": item.resolution, "basis": item.basis,
        })
        for document_id in item.document_ids:
            if document_id not in node["document_ids"]:
                node["document_ids"].append(document_id)
        node["basis"] = item.basis
        if len(node["document_ids"]) > 1:
            node["resolution"] = "identifier_match"
        graph_nodes.setdefault(item.canonical_id, {
            "id": item.canonical_id,
            "kind": "entity",
            "label": item.label,
            "type": item.entity_type,
            "status": item.review_status,
            "confidence": None,
            "document_ids": [],
            "resolution": item.resolution,
            "metadata": {
                "basis": item.basis,
                "review_status": item.review_status,
                "review_reason": item.review_reason,
                "reviewed_at": item.reviewed_at.isoformat() if item.reviewed_at else None,
            },
        })
        graph_nodes[item.canonical_id]["status"] = item.review_status
        graph_nodes[item.canonical_id]["document_ids"] = node["document_ids"]
        graph_nodes[item.canonical_id]["metadata"]["basis"] = item.basis
        for document_id in item.document_ids:
            document = document_by_id.get(document_id)
            if document is None:
                continue
            graph_edges.append({
                "id": f"identifier:{document_id}:{item.canonical_id}",
                "source": f"document:{document_id}",
                "target": item.canonical_id,
                "relationship_type": "same_transaction",
                "label": "shared identifier",
                "type": "SAME_TRANSACTION",
                "kind": "explicit",
                "confidence": None,
                "basis": item.basis,
                "analyzer": "deterministic_identifier_match",
                "status": "candidate",
                "evidence_refs": [{
                    "document_id": document_id,
                    "filename": document.filename,
                    "source_sha256": document.sha256,
                    "field_type": "identifier",
                }],
            })
    findings = (await db.scalars(select(Finding).where(Finding.investigation_id == investigation_id))).all()
    timeline = (await db.scalars(select(TimelineEvent).where(TimelineEvent.investigation_id == investigation_id))).all()
    for finding in findings:
        graph_id = f"finding:{finding.id}"
        graph_nodes[graph_id] = {
            "id": graph_id,
            "kind": "finding",
            "label": finding.title,
            "type": finding.finding_type,
            "status": finding.status,
            "confidence": finding.confidence,
            "metadata": {"severity": finding.severity, "analyzer": finding.analyzer_name, "method": finding.method},
        }
        for index, ref in enumerate(finding.evidence_refs or []):
            document_id = ref.get("document_id")
            if document_id in document_by_id:
                graph_edges.append({
                    "id": f"finding-evidence:{finding.id}:{index}",
                    "source": f"document:{document_id}",
                    "target": graph_id,
                    "relationship_type": "supports",
                    "label": "cited by finding",
                    "type": "SUPPORTS",
                    "kind": "explicit",
                    "confidence": finding.confidence,
                    "basis": "Finding cites this document as supporting evidence.",
                    "analyzer": finding.analyzer_name,
                    "status": finding.status,
                    "evidence_refs": [ref],
                })
    for event in timeline:
        graph_id = f"event:{event.id}"
        graph_nodes[graph_id] = {
            "id": graph_id,
            "kind": "event",
            "label": event.raw_date,
            "type": event.event_type,
            "status": "candidate",
            "confidence": event.confidence,
            "metadata": {"interpreted_date": event.interpreted_date, "ambiguous": event.ambiguous, "description": event.description},
        }
        for index, ref in enumerate(event.evidence_refs or []):
            document_id = ref.get("document_id")
            if document_id in document_by_id:
                graph_edges.append({
                    "id": f"event-evidence:{event.id}:{index}",
                    "source": f"document:{document_id}",
                    "target": graph_id,
                    "relationship_type": "chronologically_precedes",
                    "label": "date found in",
                    "type": "DATE_FOUND_IN",
                    "kind": "explicit",
                    "confidence": event.confidence,
                    "basis": "Timeline date was extracted from the cited document. No causal inference is made.",
                    "analyzer": "deterministic_date_extraction",
                    "status": "candidate",
                    "evidence_refs": [ref],
                })
    return {
        "nodes": list(nodes.values()),
        "edges": edges,
        "graph_nodes": list(graph_nodes.values()),
        "graph_edges": graph_edges,
        "meta": {"investigation_id": str(investigation_id), "graph_version": "graph_v2"},
    }


@router.get("/{investigation_id}/relationships")
async def relationship_list(investigation_id: UUID, limit: int = Query(100, ge=1, le=500), offset: int = Query(0, ge=0), db: AsyncSession = Depends(get_db), user: User = Depends(require_permission("investigations:read"))):
    graph = await relationship_graph(investigation_id, db, user)
    return {"items": graph["edges"][offset:offset + limit], "offset": offset, "limit": limit, "total": len(graph["edges"])}


@router.get("/{investigation_id}/relationships/search")
async def search_relationship_entities(investigation_id: UUID, q: str = Query(..., min_length=1, max_length=120), limit: int = Query(50, ge=1, le=100), db: AsyncSession = Depends(get_db), user: User = Depends(require_permission("investigations:read"))):
    graph = await relationship_graph(investigation_id, db, user)
    needle = q.casefold().strip()
    return {"items": [node for node in graph["nodes"] if needle in node["label"].casefold()][:limit]}


@router.get("/{investigation_id}/relationships/entities/{entity_id}")
async def relationship_entity(investigation_id: UUID, entity_id: str, db: AsyncSession = Depends(get_db), user: User = Depends(require_permission("investigations:read"))):
    graph = await relationship_graph(investigation_id, db, user)
    node = next((item for item in graph["nodes"] if item["id"] == entity_id), None)
    if node is None:
        raise HTTPException(status_code=404, detail="Relationship entity not found")
    return {"node": node, "relationships": [edge for edge in graph["edges"] if entity_id in {edge["source"], edge["target"]}]}


@router.get("/{investigation_id}/relationships/entities/{entity_id}/neighbors")
async def relationship_entity_neighbors(investigation_id: UUID, entity_id: str, limit: int = Query(50, ge=1, le=100), db: AsyncSession = Depends(get_db), user: User = Depends(require_permission("investigations:read"))):
    graph = await relationship_graph(investigation_id, db, user)
    if not any(item["id"] == entity_id for item in graph["nodes"]):
        raise HTTPException(status_code=404, detail="Relationship entity not found")
    edges = [edge for edge in graph["edges"] if entity_id in {edge["source"], edge["target"]}][:limit]
    neighbor_ids = {edge["target"] if edge["source"] == entity_id else edge["source"] for edge in edges}
    return {"nodes": [node for node in graph["nodes"] if node["id"] in neighbor_ids], "edges": edges}

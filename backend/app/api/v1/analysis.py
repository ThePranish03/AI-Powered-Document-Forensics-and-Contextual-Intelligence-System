import asyncio
import hashlib
from datetime import datetime, timezone
from pathlib import Path
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_db, require_csrf, require_permission
from app.api.v1.investigations import owned_investigation, stored_document_path
from app.core.config import settings
from app.models.entities import AuditChainEvent, DerivedGraphNode, Document, DossierSnapshot, ExtractionRun, Finding, OCRResult, OcrEvaluationRun, TimelineEvent, User
from app.services.audit_chain import append_audit, verify_audit_chain
from app.services.investigation_analysis import _finding_dict, _timeline_dict, create_dossier, run_investigation_analysis
from app.services.ocr_metrics import evaluate_samples

router = APIRouter()
REVIEW_STATUSES = {"accepted", "rejected", "dismissed", "needs_review"}


class FindingReview(BaseModel):
    status: str
    review_notes: str | None = Field(default=None, max_length=4000)


class TimelineNote(BaseModel):
    annotation: str = Field(min_length=1, max_length=4000)


class EvaluationSample(BaseModel):
    id: str = Field(min_length=1, max_length=80)
    ground_truth: str = ""
    hypothesis: str = ""
    document_type: str | None = None
    quality: str | None = None
    ground_truth_boxes: list | None = None
    predicted_boxes: list | None = None


class EvaluationRequest(BaseModel):
    dataset_name: str = Field(min_length=1, max_length=160)
    configuration: dict = Field(default_factory=dict)
    samples: list[EvaluationSample] = Field(min_length=1, max_length=200)


class DocumentEvaluationSample(BaseModel):
    document_id: UUID
    ground_truth: str = Field(max_length=100_000)
    ground_truth_boxes: list | None = None


class DocumentEvaluationRequest(BaseModel):
    dataset_name: str = Field(min_length=1, max_length=160)
    configuration: dict = Field(default_factory=dict)
    samples: list[DocumentEvaluationSample] = Field(min_length=1, max_length=200)


class SearchRequest(BaseModel):
    query: str = Field(min_length=1, max_length=300)


def _dossier_summary(item: DossierSnapshot) -> dict:
    return {
        "id": str(item.id),
        "version": item.version,
        "created_at": item.created_at.isoformat(),
        "snapshot": item.snapshot,
        "html": item.html,
    }


@router.post("/{investigation_id}/analysis-runs", status_code=201)
async def start_analysis(investigation_id: UUID, request: Request, db: AsyncSession = Depends(get_db), user: User = Depends(require_permission("investigations:update"))):
    require_csrf(request)
    investigation = await owned_investigation(db, user, investigation_id)
    return await run_investigation_analysis(db, investigation, user.id)


@router.get("/{investigation_id}/findings")
async def list_findings(
    investigation_id: UUID,
    status_filter: str | None = Query(default=None, alias="status"),
    finding_type: str | None = None,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_permission("investigations:read")),
):
    await owned_investigation(db, user, investigation_id)
    query = select(Finding).where(Finding.investigation_id == investigation_id)
    if status_filter:
        query = query.where(Finding.status == status_filter)
    if finding_type:
        query = query.where(Finding.finding_type == finding_type)
    rows = (await db.scalars(query.order_by(Finding.created_at.desc()))).all()
    return {"items": [_finding_dict(item) for item in rows]}


@router.patch("/{investigation_id}/findings/{finding_id}")
async def review_finding(investigation_id: UUID, finding_id: UUID, payload: FindingReview, request: Request, db: AsyncSession = Depends(get_db), user: User = Depends(require_permission("investigations:update"))):
    require_csrf(request)
    await owned_investigation(db, user, investigation_id)
    if payload.status not in REVIEW_STATUSES:
        raise HTTPException(status_code=422, detail="Review status must be accepted, rejected, dismissed, or needs_review")
    finding = await db.scalar(select(Finding).where(Finding.id == finding_id, Finding.investigation_id == investigation_id))
    if finding is None:
        raise HTTPException(status_code=404, detail="Finding not found")
    finding.status = payload.status
    finding.review_notes = payload.review_notes.strip() if payload.review_notes else None
    finding.reviewed_by_user_id = user.id
    finding.reviewed_at = datetime.now(timezone.utc)
    await append_audit(db, investigation_id=investigation_id, actor_user_id=user.id, action="finding.reviewed", target_type="finding", target_id=str(finding.id), details={"status": finding.status})
    await db.commit()
    await db.refresh(finding)
    return _finding_dict(finding)


@router.get("/{investigation_id}/timeline")
async def list_timeline(investigation_id: UUID, db: AsyncSession = Depends(get_db), user: User = Depends(require_permission("investigations:read"))):
    await owned_investigation(db, user, investigation_id)
    rows = (await db.scalars(select(TimelineEvent).where(TimelineEvent.investigation_id == investigation_id))).all()
    items = [_timeline_dict(item) for item in rows]
    items.sort(key=lambda item: (item["interpreted_date"] is None, item["interpreted_date"] or "", item["raw_date"]))
    return {"items": items, "note": "Ambiguous dates stay unordered by an assumed day-month reading. No timezone is inferred."}


@router.patch("/{investigation_id}/timeline/{event_id}")
async def annotate_timeline(investigation_id: UUID, event_id: UUID, payload: TimelineNote, request: Request, db: AsyncSession = Depends(get_db), user: User = Depends(require_permission("investigations:update"))):
    require_csrf(request)
    await owned_investigation(db, user, investigation_id)
    event = await db.scalar(select(TimelineEvent).where(TimelineEvent.id == event_id, TimelineEvent.investigation_id == investigation_id))
    if event is None:
        raise HTTPException(status_code=404, detail="Timeline event not found")
    event.annotation = payload.annotation.strip()
    await append_audit(db, investigation_id=investigation_id, actor_user_id=user.id, action="timeline.annotated", target_type="timeline_event", target_id=str(event.id), details={})
    await db.commit()
    await db.refresh(event)
    return _timeline_dict(event)


@router.get("/{investigation_id}/audit")
async def list_audit(investigation_id: UUID, db: AsyncSession = Depends(get_db), user: User = Depends(require_permission("investigations:read"))):
    await owned_investigation(db, user, investigation_id)
    rows = (await db.scalars(
        select(AuditChainEvent)
        .where(AuditChainEvent.investigation_id == investigation_id)
        .order_by(AuditChainEvent.created_at.asc(), AuditChainEvent.id.asc())
    )).all()
    return {
        "items": [{
            "id": str(item.id),
            "action": item.action,
            "target_type": item.target_type,
            "target_id": item.target_id,
            "details": item.details,
            "previous_hash": item.previous_hash,
            "event_hash": item.event_hash,
            "created_at": item.created_at.isoformat(),
        } for item in rows],
        "verification": verify_audit_chain(rows),
        "note": "The hash chain makes edits detectable. It is tamper-evident, not tamper-proof.",
    }


@router.get("/{investigation_id}/documents/{document_id}/integrity")
async def verify_integrity(investigation_id: UUID, document_id: UUID, db: AsyncSession = Depends(get_db), user: User = Depends(require_permission("investigations:read"))):
    await owned_investigation(db, user, investigation_id)
    document = await db.scalar(select(Document).where(Document.id == document_id, Document.investigation_id == investigation_id))
    if document is None or not document.storage_key or not document.sha256:
        raise HTTPException(status_code=404, detail="Document not found")
    path = stored_document_path(document, require_exists=False)
    if not path.is_file():
        return {"document_id": str(document.id), "status": "unavailable", "reason": "Stored bytes could not be read.", "sha256": document.sha256, "match": None}
    digest = await asyncio.to_thread(_sha256_file, path)
    return {"document_id": str(document.id), "status": "verified" if digest == document.sha256 else "mismatch", "sha256": document.sha256, "computed_sha256": digest, "match": digest == document.sha256}


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


@router.post("/{investigation_id}/dossiers", status_code=201)
async def generate_dossier(investigation_id: UUID, request: Request, db: AsyncSession = Depends(get_db), user: User = Depends(require_permission("investigations:update"))):
    require_csrf(request)
    investigation = await owned_investigation(db, user, investigation_id)
    record = await create_dossier(db, investigation, user.id)
    return _dossier_summary(record)


@router.get("/{investigation_id}/dossiers")
async def list_dossiers(investigation_id: UUID, db: AsyncSession = Depends(get_db), user: User = Depends(require_permission("investigations:read"))):
    await owned_investigation(db, user, investigation_id)
    rows = (await db.scalars(select(DossierSnapshot).where(DossierSnapshot.investigation_id == investigation_id).order_by(DossierSnapshot.version.desc()))).all()
    return {"items": [{"id": str(item.id), "version": item.version, "created_at": item.created_at.isoformat()} for item in rows]}


@router.get("/{investigation_id}/dossiers/{dossier_id}")
async def get_dossier(investigation_id: UUID, dossier_id: UUID, db: AsyncSession = Depends(get_db), user: User = Depends(require_permission("investigations:read"))):
    await owned_investigation(db, user, investigation_id)
    record = await db.scalar(select(DossierSnapshot).where(DossierSnapshot.id == dossier_id, DossierSnapshot.investigation_id == investigation_id))
    if record is None:
        raise HTTPException(status_code=404, detail="Dossier not found")
    return _dossier_summary(record)


@router.post("/{investigation_id}/ocr-evaluations", status_code=201)
async def create_ocr_evaluation(investigation_id: UUID, payload: EvaluationRequest, request: Request, db: AsyncSession = Depends(get_db), user: User = Depends(require_permission("investigations:update"))):
    require_csrf(request)
    await owned_investigation(db, user, investigation_id)
    metrics = evaluate_samples([sample.model_dump() for sample in payload.samples])
    record = OcrEvaluationRun(
        investigation_id=investigation_id,
        dataset_name=payload.dataset_name.strip(),
        configuration=payload.configuration,
        metrics={key: value for key, value in metrics.items() if key != "samples"},
        samples=metrics["samples"],
        created_by_user_id=user.id,
    )
    db.add(record)
    await db.flush()
    await append_audit(db, investigation_id=investigation_id, actor_user_id=user.id, action="ocr_evaluation.created", target_type="ocr_evaluation", target_id=str(record.id), details={"dataset_name": record.dataset_name, "sample_count": metrics["sample_count"]})
    await db.commit()
    await db.refresh(record)
    return {"id": str(record.id), "dataset_name": record.dataset_name, "configuration": record.configuration, "metrics": record.metrics, "samples": record.samples, "created_at": record.created_at.isoformat()}


@router.post("/{investigation_id}/ocr-evaluations/benchmark", status_code=201)
async def benchmark_stored_ocr(investigation_id: UUID, payload: DocumentEvaluationRequest, request: Request, db: AsyncSession = Depends(get_db), user: User = Depends(require_permission("investigations:update"))):
    require_csrf(request)
    await owned_investigation(db, user, investigation_id)
    document_ids = [sample.document_id for sample in payload.samples]
    if len(set(document_ids)) != len(document_ids):
        raise HTTPException(status_code=422, detail="A document may appear only once in a benchmark run")

    documents = (await db.execute(
        select(Document, OCRResult)
        .outerjoin(OCRResult, OCRResult.document_id == Document.id)
        .where(Document.investigation_id == investigation_id, Document.id.in_(document_ids))
    )).all()
    by_id = {document.id: (document, result) for document, result in documents}
    if len(by_id) != len(document_ids):
        raise HTTPException(status_code=404, detail="One or more benchmark documents were not found in this investigation")
    missing_ocr = [document_id for document_id, (_, result) in by_id.items() if result is None]
    if missing_ocr:
        raise HTTPException(status_code=409, detail="OCR results are not available for every selected document")

    extraction_runs = (await db.scalars(
        select(ExtractionRun)
        .where(ExtractionRun.document_id.in_(document_ids), ExtractionRun.status == "completed")
        .order_by(ExtractionRun.run_number.desc())
    )).all()
    latest_run: dict[UUID, ExtractionRun] = {}
    for extraction in extraction_runs:
        latest_run.setdefault(extraction.document_id, extraction)

    samples = []
    for sample in payload.samples:
        document, result = by_id[sample.document_id]
        if result is None:
            raise HTTPException(status_code=409, detail=f"OCR result is not available for {document.filename}")
        extraction = latest_run.get(document.id)
        blocks = result.blocks or []
        samples.append({
            "id": str(document.id),
            "document_id": str(document.id),
            "filename": document.filename,
            "document_type": document.content_type,
            "ground_truth": sample.ground_truth,
            "hypothesis": result.recognized_text or "\n".join(
                str(block.get("text") or "") for block in blocks if isinstance(block, dict)
            ),
            "ground_truth_boxes": sample.ground_truth_boxes,
            "predicted_boxes": [block.get("engine_bbox") for block in blocks if isinstance(block, dict) and block.get("engine_bbox") is not None],
            "provider": extraction.provider if extraction else result.provider,
            "provider_version": extraction.provider_version if extraction else result.provider_version,
            "extraction_run_id": str(extraction.id) if extraction else None,
            "latency_ms": extraction.latency_ms if extraction else None,
        })

    metrics = evaluate_samples(samples)
    run_configuration = {
        **payload.configuration,
        "evaluation_source": "stored_ocr_results",
        "reprocessed_documents": False,
        "extraction_run_ids": [sample["extraction_run_id"] for sample in samples],
    }
    record = OcrEvaluationRun(
        investigation_id=investigation_id,
        dataset_name=payload.dataset_name.strip(),
        configuration=run_configuration,
        metrics={key: value for key, value in metrics.items() if key != "samples"},
        samples=metrics["samples"],
        created_by_user_id=user.id,
    )
    db.add(record)
    await db.flush()
    await append_audit(
        db,
        investigation_id=investigation_id,
        actor_user_id=user.id,
        action="ocr_evaluation.benchmark_created",
        target_type="ocr_evaluation",
        target_id=str(record.id),
        details={"dataset_name": record.dataset_name, "sample_count": metrics["sample_count"]},
    )
    await db.commit()
    await db.refresh(record)
    return {
        "id": str(record.id),
        "dataset_name": record.dataset_name,
        "configuration": record.configuration,
        "metrics": record.metrics,
        "samples": record.samples,
        "created_at": record.created_at.isoformat(),
    }


@router.get("/{investigation_id}/ocr-evaluations")
async def list_ocr_evaluations(investigation_id: UUID, db: AsyncSession = Depends(get_db), user: User = Depends(require_permission("investigations:read"))):
    await owned_investigation(db, user, investigation_id)
    rows = (await db.scalars(select(OcrEvaluationRun).where(OcrEvaluationRun.investigation_id == investigation_id).order_by(OcrEvaluationRun.created_at.desc()))).all()
    return {"items": [{"id": str(item.id), "dataset_name": item.dataset_name, "metrics": item.metrics, "created_at": item.created_at.isoformat()} for item in rows]}


@router.post("/{investigation_id}/search")
async def search_evidence(investigation_id: UUID, payload: SearchRequest, request: Request, db: AsyncSession = Depends(get_db), user: User = Depends(require_permission("investigations:read"))):
    require_csrf(request)
    await owned_investigation(db, user, investigation_id)
    needle = payload.query.casefold().strip()
    citations = []
    rows = (await db.execute(
        select(Document, OCRResult)
        .outerjoin(OCRResult, OCRResult.document_id == Document.id)
        .where(Document.investigation_id == investigation_id)
    )).all()
    for document, result in rows:
        if result is None:
            continue
        for block in result.blocks or []:
            text = str(block.get("text") or "")
            if needle in text.casefold():
                citations.append({
                    "kind": "ocr_block",
                    "document_id": str(document.id),
                    "filename": document.filename,
                    "page_number": block["page_index"] + 1 if isinstance(block.get("page_index"), int) and not isinstance(block.get("page_index"), bool) else block.get("page_index"),
                    "block_id": block.get("block_id"),
                    "quoted_text": text[:500],
                    "sha256": document.sha256,
                })
                if len(citations) >= 25:
                    break
        if len(citations) >= 25:
            break
    findings = (await db.scalars(select(Finding).where(Finding.investigation_id == investigation_id))).all()
    for finding in findings:
        haystack = f"{finding.title}\n{finding.description}".casefold()
        if needle in haystack:
            citations.append({"kind": "finding", "finding_id": str(finding.id), "title": finding.title, "status": finding.status})
    if not citations:
        answer = "The evidence stored for this investigation does not contain that text."
    else:
        answer = f"Retrieved {len(citations)} citation(s) containing “{payload.query.strip()}”. This listing is retrieval, not a conclusion."
    return {
        "query": payload.query.strip(),
        "answer": answer,
        "citations": citations,
        "llm": {
            "used": False,
            "status": "unavailable" if not settings.xai_api_key else "not_used",
            "reason": "No language-model provider is configured." if not settings.xai_api_key else "Search returns stored evidence only. A model narrative is not generated for this request.",
        },
        "conflicts": [ _finding_dict(item) for item in findings if item.finding_type == "identifier_amount_conflict" and needle in f"{item.title} {item.description}".casefold() ],
    }


@router.get("/{investigation_id}/derived-nodes")
async def derived_nodes(investigation_id: UUID, db: AsyncSession = Depends(get_db), user: User = Depends(require_permission("investigations:read"))):
    await owned_investigation(db, user, investigation_id)
    rows = (await db.scalars(select(DerivedGraphNode).where(DerivedGraphNode.investigation_id == investigation_id))).all()
    return {"items": [{"id": item.canonical_id, "label": item.label, "type": item.entity_type, "document_ids": item.document_ids, "resolution": item.resolution, "basis": item.basis} for item in rows]}

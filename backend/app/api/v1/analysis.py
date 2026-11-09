import asyncio
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse, Response
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_db, require_csrf, require_permission
from app.api.v1.investigations import owned_investigation, stored_document_path
from app.core.config import settings
from app.models.entities import AuditChainEvent, DerivedGraphNode, Document, DossierSnapshot, EvidencePage, EvidenceSpan, ExtractionRun, Finding, OCRResult, OcrEvaluationRun, TimelineEvent, User, VisualAnalysisRun
from app.services.audit_chain import append_audit, verify_audit_chain
from app.services.dossier_report import render_pdf, snapshot_digest
from app.services.investigation_analysis import _finding_dict, _timeline_dict, create_dossier, run_investigation_analysis
from app.services.ocr_metrics import build_cross_document_correlation, evaluate_samples
from app.services.visual_forensics import analyze_file

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


class CorrelationReview(BaseModel):
    status: str
    reason: str = Field(min_length=1, max_length=4000)


async def _correlation_data(db: AsyncSession, investigation_id: UUID) -> dict:
    rows = (await db.execute(
        select(Document, OCRResult)
        .outerjoin(OCRResult, OCRResult.document_id == Document.id)
        .where(Document.investigation_id == investigation_id)
        .order_by(Document.created_at, Document.id)
    )).all()
    documents = [{
        "document_id": str(document.id),
        "filename": document.filename,
        "sha256": document.sha256,
        "provider": result.provider if result else None,
        "provider_version": result.provider_version if result else None,
        "blocks": result.blocks if result else [],
        "text": result.recognized_text if result else "",
        "processing_status": document.processing_status,
    } for document, result in rows]
    payload = build_cross_document_correlation(documents)
    review_nodes = {
        item.canonical_id: item
        for item in (await db.scalars(select(DerivedGraphNode).where(DerivedGraphNode.investigation_id == investigation_id))).all()
    }
    for link in payload["links"]:
        item = review_nodes.get(link["id"])
        if item:
            link["review_status"] = item.review_status
            link["reviewed_at"] = item.reviewed_at.isoformat() if item.reviewed_at else None
            link["review_reason"] = item.review_reason
    payload["summary"]["confirmed_links"] = sum(link["review_status"] == "confirmed" for link in payload["links"])
    payload["summary"]["rejected_links"] = sum(link["review_status"] == "rejected" for link in payload["links"])
    payload["summary"]["unreviewed_links"] = sum(link["review_status"] == "candidate" for link in payload["links"])
    payload["summary"]["candidate_links"] = payload["summary"]["unreviewed_links"]
    return payload


def _dossier_summary(item: DossierSnapshot) -> dict:
    return {
        "id": str(item.id),
        "version": item.version,
        "created_at": item.created_at.isoformat(),
        "snapshot": item.snapshot,
        "html": item.html,
        "content_sha256": item.content_sha256,
    }


def _verify_dossier_integrity(record: DossierSnapshot) -> None:
    if snapshot_digest(record.snapshot, record.html) != record.content_sha256:
        raise HTTPException(status_code=409, detail="Stored dossier integrity verification failed")


@router.post("/{investigation_id}/analysis-runs", status_code=201)
async def start_analysis(investigation_id: UUID, request: Request, db: AsyncSession = Depends(get_db), user: User = Depends(require_permission("investigations:update"))):
    require_csrf(request)
    investigation = await owned_investigation(db, user, investigation_id)
    return await run_investigation_analysis(db, investigation, user.id)


def _visual_run_dict(run: VisualAnalysisRun) -> dict:
    return {"id": str(run.id), "investigation_id": str(run.investigation_id), "document_id": str(run.document_id), "status": run.status, "analyzer_version": run.analyzer_version, "result": run.result or {}, "error_detail": run.error_detail, "created_at": run.created_at.isoformat(), "started_at": run.started_at.isoformat() if run.started_at else None, "completed_at": run.completed_at.isoformat() if run.completed_at else None}


@router.post("/{investigation_id}/documents/{document_id}/visual-analysis-runs", status_code=201)
async def run_visual_analysis(investigation_id: UUID, document_id: UUID, request: Request, db: AsyncSession = Depends(get_db), user: User = Depends(require_permission("investigations:update"))):
    require_csrf(request)
    await owned_investigation(db, user, investigation_id)
    document = await db.scalar(select(Document).where(Document.id == document_id, Document.investigation_id == investigation_id))
    if document is None:
        raise HTTPException(status_code=404, detail="Document not found")
    path = stored_document_path(document)
    prior_run_count = await db.scalar(select(func.count(VisualAnalysisRun.id)).where(VisualAnalysisRun.investigation_id == investigation_id, VisualAnalysisRun.document_id == document_id))
    run_kind = "retry" if prior_run_count else "new"
    run = VisualAnalysisRun(investigation_id=investigation_id, document_id=document_id, status="processing", created_by_user_id=user.id, started_at=datetime.now(timezone.utc), result={"checks": [], "observations": []})
    db.add(run)
    await db.flush()
    run.result = {"checks": [], "observations": [], "run_kind": run_kind}
    await append_audit(db, investigation_id=investigation_id, actor_user_id=user.id, action="visual_analysis.started", target_type="visual_analysis_run", target_id=str(run.id), details={"document_id": str(document_id), "filename": document.filename, "run_kind": run_kind})
    await db.commit()
    try:
        result = await asyncio.to_thread(analyze_file, path, str(document.id), document.content_type)
        observations = result.get("observations", [])
        spans = (await db.scalars(select(EvidenceSpan).where(EvidenceSpan.document_id == document.id))).all()
        pages = (await db.scalars(select(EvidencePage).where(EvidencePage.document_id == document.id))).all()
        page_by_num = {page.page_number: page for page in pages}
        count = 0
        for observation in observations:
            page_no = observation.get("page_number")
            region = observation.get("regions", [None])[0]
            matched_span = None
            if region:
                for span in spans:
                    if span.page_id != (page_by_num.get(page_no).id if page_by_num.get(page_no) else None):
                        continue
                    box = span.bounding_box or {}
                    try:
                        x0, y0, x1, y1 = (float(box[k]) for k in ("x0", "y0", "x1", "y1"))
                        if x0 <= region["x1"] and x1 >= region["x0"] and y0 <= region["y1"] and y1 >= region["y0"]:
                            matched_span = span
                            break
                    except (KeyError, TypeError, ValueError):
                        continue
            evidence_refs = [{"document_id": str(document.id), "filename": document.filename, "source_sha256": document.sha256, "page_number": page_no, "page_id": str(page_by_num[page_no].id) if page_no in page_by_num else None, "block_id": matched_span.block_id if matched_span else observation.get("block_id"), "quoted_text": observation.get("quoted_text") or (matched_span.text[:500] if matched_span else None), "field_type": observation["check_type"], "bounding_box": region, "regions": observation.get("regions", []), "evidence_reference": f"document:{document.id}/page:{page_no}/visual"}]
            raw = json.dumps({"document_id": str(document.id), "finding_type": observation["finding_type"], "page": page_no, "regions": observation.get("regions", []), "metrics": observation.get("metrics", {})}, sort_keys=True)
            fingerprint = hashlib.sha256(raw.encode()).hexdigest()
            finding = await db.scalar(select(Finding).where(Finding.investigation_id == investigation_id, Finding.fingerprint == fingerprint))
            if finding is None:
                finding = Finding(investigation_id=investigation_id, finding_type=observation["finding_type"], title=observation["finding_type"].replace("visual_", "").replace("_", " ").title(), description=observation["explanation"], severity="low", confidence=0.5, status="candidate", analyzer_name="visual_forensics", analyzer_version="1", method=observation["algorithm"], fingerprint=fingerprint, evidence_refs=evidence_refs, limitations=observation["limitations"])
                db.add(finding)
                await db.flush()
            observation["finding_id"] = str(finding.id)
            observation["investigation_id"] = str(investigation_id)
            observation["document_id"] = str(document.id)
            observation["evidence_ref"] = evidence_refs[0]
            observation["review_status"] = finding.status
            count += 1
        unavailable_checks = [check["check_type"] for check in result.get("checks", []) if check.get("status") in {"unavailable", "partial", "failed"}]
        run.status = "partial" if result.get("status") == "completed" and unavailable_checks else result.get("status", "completed")
        run.result = {**result, "status": run.status, "run_kind": run_kind, "unavailable_checks": unavailable_checks, "observations_written": count}
        run.completed_at = datetime.now(timezone.utc)
        await append_audit(db, investigation_id=investigation_id, actor_user_id=user.id, action="visual_analysis.completed", target_type="visual_analysis_run", target_id=str(run.id), details={"document_id": str(document.id), "status": run.status, "observations_written": count, "checks_executed": [check["check_type"] for check in result.get("checks", []) if check.get("status") == "executed"], "checks_unavailable": unavailable_checks})
        await db.commit()
        await db.refresh(run)
        return _visual_run_dict(run)
    except Exception as exc:
        await db.rollback()
        run = await db.get(VisualAnalysisRun, run.id)
        if run:
            run.status = "failed"
            run.error_detail = str(exc)[:2000]
            run.completed_at = datetime.now(timezone.utc)
            await append_audit(db, investigation_id=investigation_id, actor_user_id=user.id, action="visual_analysis.failed", target_type="visual_analysis_run", target_id=str(run.id), details={"document_id": str(document.id), "error": run.error_detail})
            await db.commit()
            return _visual_run_dict(run)
        raise HTTPException(status_code=500, detail="Visual analysis failed") from exc


@router.get("/{investigation_id}/documents/{document_id}/visual-analysis-runs")
async def list_visual_analysis_runs(investigation_id: UUID, document_id: UUID, db: AsyncSession = Depends(get_db), user: User = Depends(require_permission("investigations:read"))):
    await owned_investigation(db, user, investigation_id)
    document = await db.scalar(select(Document).where(Document.id == document_id, Document.investigation_id == investigation_id))
    if document is None:
        raise HTTPException(status_code=404, detail="Document not found")
    rows = (await db.scalars(select(VisualAnalysisRun).where(VisualAnalysisRun.investigation_id == investigation_id, VisualAnalysisRun.document_id == document_id).order_by(VisualAnalysisRun.created_at.desc()))).all()
    return {"items": [_visual_run_dict(run) for run in rows]}


@router.get("/{investigation_id}/visual-analysis-runs")
async def list_investigation_visual_analysis_runs(investigation_id: UUID, db: AsyncSession = Depends(get_db), user: User = Depends(require_permission("investigations:read"))):
    await owned_investigation(db, user, investigation_id)
    rows = (await db.scalars(select(VisualAnalysisRun).where(VisualAnalysisRun.investigation_id == investigation_id).order_by(VisualAnalysisRun.created_at.desc()))).all()
    return {"items": [_visual_run_dict(run) for run in rows]}


@router.get("/{investigation_id}/visual-analysis-runs/{run_id}")
async def get_visual_analysis_run(investigation_id: UUID, run_id: UUID, db: AsyncSession = Depends(get_db), user: User = Depends(require_permission("investigations:read"))):
    await owned_investigation(db, user, investigation_id)
    run = await db.scalar(select(VisualAnalysisRun).where(VisualAnalysisRun.id == run_id, VisualAnalysisRun.investigation_id == investigation_id))
    if run is None:
        raise HTTPException(status_code=404, detail="Visual analysis run not found")
    return _visual_run_dict(run)


@router.get("/{investigation_id}/correlation")
async def get_cross_document_correlation(investigation_id: UUID, db: AsyncSession = Depends(get_db), user: User = Depends(require_permission("investigations:read"))):
    await owned_investigation(db, user, investigation_id)
    return await _correlation_data(db, investigation_id)


@router.post("/{investigation_id}/correlation-runs", status_code=201)
async def rerun_cross_document_correlation(investigation_id: UUID, request: Request, db: AsyncSession = Depends(get_db), user: User = Depends(require_permission("investigations:update"))):
    require_csrf(request)
    investigation = await owned_investigation(db, user, investigation_id)
    result = await run_investigation_analysis(db, investigation, user.id)
    await append_audit(
        db,
        investigation_id=investigation.id,
        actor_user_id=user.id,
        action="correlation.completed",
        target_type="investigation",
        target_id=str(investigation.id),
        details={"method": "deterministic_exact_identifier_v1", "documents_considered": result["documents_considered"]},
    )
    await db.commit()
    return await _correlation_data(db, investigation_id)


@router.patch("/{investigation_id}/correlation/{canonical_id}/review")
async def review_correlation_candidate(investigation_id: UUID, canonical_id: str, payload: CorrelationReview, request: Request, db: AsyncSession = Depends(get_db), user: User = Depends(require_permission("investigations:update"))):
    require_csrf(request)
    await owned_investigation(db, user, investigation_id)
    if payload.status not in {"confirmed", "rejected"}:
        raise HTTPException(status_code=422, detail="Correlation review status must be confirmed or rejected")
    correlation = await _correlation_data(db, investigation_id)
    link = next((item for item in correlation["links"] if item["id"] == canonical_id), None)
    if link is None:
        raise HTTPException(status_code=404, detail="Correlation candidate not found")
    node = await db.scalar(select(DerivedGraphNode).where(
        DerivedGraphNode.investigation_id == investigation_id,
        DerivedGraphNode.canonical_id == canonical_id,
    ))
    if node is None:
        node = DerivedGraphNode(
            investigation_id=investigation_id,
            canonical_id=canonical_id,
            label=link["normalized_identifier"],
            entity_type=link["identifier_type"],
            document_ids=link["document_ids"],
            resolution="identifier_match",
            basis=link["explanation"],
        )
        db.add(node)
    node.review_status = payload.status
    node.reviewed_by_user_id = user.id
    node.reviewed_at = datetime.now(timezone.utc)
    node.review_reason = payload.reason.strip()
    if not node.review_reason:
        raise HTTPException(status_code=422, detail="Review reason must not be blank")
    await append_audit(
        db,
        investigation_id=investigation_id,
        actor_user_id=user.id,
        action="correlation.reviewed",
        target_type="correlation_candidate",
        target_id=canonical_id,
        details={"status": payload.status, "reason": node.review_reason},
    )
    await db.commit()
    return {
        "id": canonical_id,
        "review_status": node.review_status,
        "reviewed_at": node.reviewed_at.isoformat(),
        "review_reason": node.review_reason,
    }


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
            "actor_user_id": str(item.actor_user_id) if item.actor_user_id else None,
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
    _verify_dossier_integrity(record)
    return _dossier_summary(record)


@router.get("/{investigation_id}/dossiers/{dossier_id}/export.json")
async def export_dossier_json(investigation_id: UUID, dossier_id: UUID, db: AsyncSession = Depends(get_db), user: User = Depends(require_permission("investigations:read"))):
    await owned_investigation(db, user, investigation_id)
    record = await db.scalar(select(DossierSnapshot).where(
        DossierSnapshot.id == dossier_id,
        DossierSnapshot.investigation_id == investigation_id,
    ))
    if record is None:
        raise HTTPException(status_code=404, detail="Dossier not found")
    _verify_dossier_integrity(record)
    await append_audit(
        db,
        investigation_id=investigation_id,
        actor_user_id=user.id,
        action="dossier.exported",
        target_type="dossier",
        target_id=str(record.id),
        details={"format": "json", "version": record.version, "timestamp": datetime.now(timezone.utc).isoformat(), "actor_user_id": str(user.id), "investigation_id": str(investigation_id)},
    )
    await db.commit()
    return JSONResponse(
        content=record.snapshot,
        headers={"Content-Disposition": f'attachment; filename="dossier-v{record.version}.json"', "X-Report-Content-SHA256": record.content_sha256, "X-Report-Version": str(record.version)},
    )


@router.get("/{investigation_id}/dossiers/{dossier_id}/export.html")
async def export_dossier_html(investigation_id: UUID, dossier_id: UUID, db: AsyncSession = Depends(get_db), user: User = Depends(require_permission("investigations:read"))):
    await owned_investigation(db, user, investigation_id)
    record = await db.scalar(select(DossierSnapshot).where(
        DossierSnapshot.id == dossier_id,
        DossierSnapshot.investigation_id == investigation_id,
    ))
    if record is None:
        raise HTTPException(status_code=404, detail="Dossier not found")
    _verify_dossier_integrity(record)
    await append_audit(
        db,
        investigation_id=investigation_id,
        actor_user_id=user.id,
        action="dossier.exported",
        target_type="dossier",
        target_id=str(record.id),
        details={"format": "html", "version": record.version, "timestamp": datetime.now(timezone.utc).isoformat(), "actor_user_id": str(user.id), "investigation_id": str(investigation_id)},
    )
    await db.commit()
    return HTMLResponse(
        content=record.html,
        headers={"Content-Disposition": f'attachment; filename="dossier-v{record.version}.html"', "X-Report-Content-SHA256": record.content_sha256, "X-Report-Version": str(record.version)},
    )


@router.get("/{investigation_id}/dossiers/{dossier_id}/export.pdf")
async def export_dossier_pdf(investigation_id: UUID, dossier_id: UUID, db: AsyncSession = Depends(get_db), user: User = Depends(require_permission("investigations:read"))):
    await owned_investigation(db, user, investigation_id)
    record = await db.scalar(select(DossierSnapshot).where(
        DossierSnapshot.id == dossier_id,
        DossierSnapshot.investigation_id == investigation_id,
    ))
    if record is None:
        raise HTTPException(status_code=404, detail="Dossier not found")
    _verify_dossier_integrity(record)
    try:
        pdf_bytes = await asyncio.to_thread(render_pdf, record.snapshot)
    except Exception as exc:
        raise HTTPException(status_code=500, detail="PDF report generation failed") from exc
    await append_audit(
        db,
        investigation_id=investigation_id,
        actor_user_id=user.id,
        action="dossier.exported",
        target_type="dossier",
        target_id=str(record.id),
        details={"format": "pdf", "version": record.version, "timestamp": datetime.now(timezone.utc).isoformat(), "actor_user_id": str(user.id), "investigation_id": str(investigation_id)},
    )
    await db.commit()
    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="dossier-v{record.version}.pdf"', "X-Report-Content-SHA256": record.content_sha256, "X-Report-Version": str(record.version)},
    )


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

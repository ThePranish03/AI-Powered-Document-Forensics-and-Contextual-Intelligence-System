import asyncio
import hashlib
import json
import shutil
from datetime import datetime, timezone
from pathlib import Path
from time import perf_counter
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, Response
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_db, require_csrf, require_permission
from app.api.v1.investigations import owned_investigation, stored_document_path
from app.core.config import settings
from app.models.entities import AuditChainEvent, DerivedGraphNode, Document, DossierSnapshot, EvidencePage, EvidenceSpan, ExtractionRun, Finding, OCRResult, OcrEvaluationRun, PdfForensicArtifact, PdfForensicRun, RelationshipAnalysis, RelationshipCandidate, TimelineEvent, User, VisualAnalysisArtifact, VisualAnalysisPage, VisualAnalysisRun, VisualCandidateRegion
from app.services.audit_chain import append_audit, verify_audit_chain
from app.services.dossier_report import render_pdf, snapshot_digest
from app.services.investigation_analysis import _finding_dict, _timeline_dict, create_dossier, run_investigation_analysis
from app.services.evidence_correlation import correlate_pdf_text_and_visual
from app.services.ocr_metrics import evaluate_samples
from app.services.pdf_forensics import analyze_pdf_isolated, compare_pdf_native_text_to_ocr
from app.services.relationship_index import _candidate_key, build_comparison_views
from app.services.relationship_review import consistent_legacy_review_field, project_candidate_review, resolve_legacy_candidate_review, review_history_status
from app.services.visual_forensics import analyze_file
from app.services.visual_forensics_engine import engine_health_payload
from app.services.visual_forensics_pipeline import run_engine_pipeline

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


class VisualRegionReview(BaseModel):
    status: str
    review_notes: str | None = Field(default=None, max_length=4000)


def _pdf_forensic_run_payload(run: PdfForensicRun) -> dict:
    return {
        "id": str(run.id),
        "investigation_id": str(run.investigation_id),
        "document_id": str(run.document_id),
        "source_sha256": run.source_sha256,
        "status": run.status,
        "parser": {"name": run.parser_name, "version": run.parser_version},
        "created_at": run.created_at.isoformat(),
        "result": run.result,
    }


def _pdf_artifact_run_dir(run_id: UUID) -> Path:
    root = Path(settings.document_storage_path).resolve() / "pdf_artifacts"
    candidate = root / run_id.hex
    if candidate.is_symlink():
        raise HTTPException(status_code=500, detail="PDF artifact storage is unavailable")
    target = candidate.resolve()
    if target.parent != root.resolve():
        raise HTTPException(status_code=500, detail="PDF artifact storage is unavailable")
    return target


async def _correlation_data(db: AsyncSession, investigation_id: UUID) -> dict:
    started = perf_counter()
    documents = (await db.scalars(select(Document).where(Document.investigation_id == investigation_id))).all()
    document_ids = [item.id for item in documents]
    documents_by_id = {str(item.id): item for item in documents}
    documents_with_ocr = await db.scalar(select(func.count(func.distinct(OCRResult.document_id))).where(
        OCRResult.document_id.in_(document_ids),
    )) if document_ids else 0
    comparison_facts_by_document = {}
    if document_ids:
        relationship_rows = (await db.scalars(select(RelationshipAnalysis).where(
            RelationshipAnalysis.document_id.in_(document_ids),
        ))).all()
        for record in relationship_rows:
            payload = record.payload if isinstance(record.payload, dict) else {}
            deterministic = payload.get("deterministic_analysis") if isinstance(payload.get("deterministic_analysis"), dict) else {}
            comparison_facts = deterministic.get("comparison_facts")
            current_document = documents_by_id.get(str(record.document_id))
            if (
                isinstance(comparison_facts, dict)
                and current_document is not None
                and comparison_facts.get("source_sha256") == current_document.sha256
            ):
                comparison_facts_by_document[str(record.document_id)] = comparison_facts
    candidates = (await db.scalars(select(RelationshipCandidate).where(
        RelationshipCandidate.investigation_id == investigation_id,
    ).order_by(RelationshipCandidate.created_at, RelationshipCandidate.candidate_key))).all()
    indexed_candidate_keys = {item.candidate_key for item in candidates}
    review_nodes = {
        item.canonical_id: item
        for item in (await db.scalars(select(DerivedGraphNode).where(DerivedGraphNode.investigation_id == investigation_id))).all()
    }
    links = []
    for item in candidates:
        stable_id = "detrel:" + item.candidate_key[:24]
        current_refs = [
            ref for ref in (item.evidence_refs or [])
            if str(ref.get("document_id")) in documents_by_id
            and ref.get("source_sha256") == documents_by_id[str(ref.get("document_id"))].sha256
        ]
        current_document_ids = sorted({str(ref["document_id"]) for ref in current_refs})
        legacy_review = resolve_legacy_candidate_review(
            investigation_id=investigation_id,
            evidence_type=item.evidence_type,
            normalized_value=item.normalized_value,
            candidate_document_ids=current_document_ids,
            legacy_nodes=review_nodes.values(),
        )
        indexed_review_status = item.review_status
        projected_review = project_candidate_review(indexed_review_status, legacy_review)
        legacy_review_at = consistent_legacy_review_field(legacy_review, "reviewed_at") if legacy_review["status"] == "matched" else None
        legacy_review_by = consistent_legacy_review_field(legacy_review, "reviewed_by_user_id") if legacy_review["status"] == "matched" else None
        legacy_review_reason = consistent_legacy_review_field(legacy_review, "review_reason") if legacy_review["status"] == "matched" else None
        links.append({
            "id": stable_id, "candidate_key": item.candidate_key,
            "relation_type": "shared_case_link" if item.evidence_type == "CASE_LINK" else "shared_document_identifier" if item.evidence_type in {"INVOICE", "PURCHASE_ORDER", "TRANSACTION", "REFERENCE", "ACCOUNT", "VENDOR", "DOCUMENT_ID"} else "shared_deterministic_identifier",
            "normalized_identifier": item.normalized_value,
            "identifier_type": item.evidence_type,
            "document_ids": current_document_ids, "document_count": len(current_document_ids),
            "rank": 1.0, "rank_kind": "exact_rule_match",
            "method": "deterministic_relationship_index_v2",
            "explanation": item.explanation, "evidence_kind": "deterministic_source_match",
            "limitations": "Exact labeled identifiers are review leads; they do not establish a common party or transaction.",
            "explicit_or_inferred": "explicit", "evidence_refs": [
                {**ref, "quoted_text": ref.get("raw_value"), "field_type": ref.get("field_type") or ref.get("evidence_type")}
                for ref in current_refs
            ],
            "review_status": projected_review["review_status"],
            "indexed_review_status": indexed_review_status,
            "review_source": projected_review["review_source"],
            "reviewed_at": item.reviewed_at.isoformat() if item.reviewed_at else (legacy_review_at.isoformat() if legacy_review_at else None),
            "review_reason": item.review_reason or legacy_review_reason,
            "reviewed_by_user_id": str(item.reviewed_by_user_id) if item.reviewed_by_user_id else legacy_review_by,
            "review_mapping": legacy_review,
            "lifecycle_status": "stale" if len(current_document_ids) < 2 else item.lifecycle_status,
            "first_seen_at": item.first_seen_at.isoformat(), "last_seen_at": item.last_seen_at.isoformat(),
            "review_history": item.review_history,
            "review_history_status": review_history_status(item.review_history, legacy_review),
        })
    represented_legacy_ids = {
        "detrel:" + hashlib.sha256(f"{link['identifier_type']}|{link['normalized_identifier']}".encode()).hexdigest()[:24]
        for link in links if link.get("candidate_key")
    }
    legacy_groups = {}
    for node in review_nodes.values():
        key = _candidate_key(investigation_id, node.entity_type, node.label)
        legacy_groups.setdefault(key, []).append(node)
    for key, nodes in legacy_groups.items():
        if key in indexed_candidate_keys:
            continue
        group_documents = sorted({str(doc_id) for node in nodes for doc_id in (node.document_ids or [])})
        mapping = resolve_legacy_candidate_review(
            investigation_id=investigation_id,
            evidence_type=nodes[0].entity_type,
            normalized_value=nodes[0].label,
            candidate_document_ids=group_documents,
            legacy_nodes=nodes,
        )
        for node in sorted(nodes, key=lambda item: item.canonical_id):
            if any(link["id"] == node.canonical_id for link in links) or node.canonical_id in represented_legacy_ids:
                continue
            projected_status = "ambiguous" if mapping["status"] == "ambiguous" else mapping["review_status"]
            links.append({
                "id": node.canonical_id, "candidate_key": None, "relation_type": "legacy_candidate",
                "normalized_identifier": node.label, "identifier_type": node.entity_type,
                "document_ids": node.document_ids, "document_count": len(node.document_ids),
                "rank": 1.0, "rank_kind": node.resolution, "method": "legacy_derived_graph_node",
                "explanation": node.basis, "evidence_kind": "legacy_candidate", "evidence_refs": [],
                "review_status": projected_status, "reviewed_at": node.reviewed_at.isoformat() if node.reviewed_at else None,
                "review_reason": node.review_reason, "lifecycle_status": "legacy",
                "review_mapping": mapping, "review_history": [],
                "review_history_status": "legacy_history_unavailable",
            })
    pending = any(doc.processing_status in {"queued", "processing", "relationship_extraction_pending", "relationship_extraction_processing"} for doc in documents)
    failed = any(doc.processing_status in {"failed", "relationship_extraction_failed"} for doc in documents)
    comparisons, comparison_by_id = build_comparison_views(links, comparison_facts_by_document)
    stored_findings = (await db.scalars(select(Finding).where(
        Finding.investigation_id == investigation_id,
        Finding.finding_type == "identifier_amount_conflict",
    ))).all()
    contradictions = []
    findings_by_value = {}
    for finding in stored_findings:
        conflict = comparison_by_id.get(next((str(ref.get("identifier")) for ref in (finding.evidence_refs or []) if ref.get("identifier")), ""))
        if conflict is None:
            continue
        refs_are_current = all(
            str(ref.get("document_id")) in documents_by_id
            and ref.get("source_sha256") == documents_by_id[str(ref.get("document_id"))].sha256
            for ref in (finding.evidence_refs or [])
        )
        if refs_are_current:
            contradictions.append({**_finding_dict(finding), "finding_id": str(finding.id), "id": finding.fingerprint})
            findings_by_value[conflict["value"]] = True
    # When only the indexed facts exist, reconstruct the previous contradiction
    # view from those persisted, cited totals. This is a deterministic response
    # projection and does not read OCR blocks or write a Finding during GET.
    for value, comparison in comparison_by_id.items():
        if findings_by_value.get(value):
            continue
        link = next((item for item in links if item.get("candidate_key") and item["normalized_identifier"] == value), None)
        if link is None:
            continue
        identifier_refs = {str(ref.get("document_id")): ref for ref in link.get("evidence_refs", [])}
        evidence_refs = []
        totals = []
        for entry in comparison["documents"]:
            total = entry.get("total")
            if not total:
                continue
            total_ref = total["evidence_ref"]
            identifier_ref = identifier_refs.get(str(entry["document_id"]), {})
            totals.append(str(total.get("raw")))
            evidence_refs.append({
                **total_ref, "identifier": value,
                "identifier_block_id": identifier_ref.get("block_id"),
                "related_total": total.get("raw"),
                "normalized_total": total.get("value"),
                "normalization_warnings": [],
            })
        if len(totals) < 2:
            continue
        fingerprint = f"conflict:{value}"
        contradictions.append({
            "id": fingerprint, "finding_type": "identifier_amount_conflict",
            "title": f"Identifier {value} has conflicting labeled totals",
            "description": "The same typed identifier is paired with different persisted labeled totals: " + "; ".join(totals) + ". This is a candidate discrepancy to review, not proof that either document is false.",
            "severity": "high", "confidence": 0.7, "status": "candidate",
            "analyzer_name": "deterministic_evidence", "analyzer_version": "1",
            "method": "shared_identifier_total_conflict", "evidence_refs": evidence_refs,
            "limitations": "Totals are OCR/native extracted values; verify them against original source evidence.",
            "review_notes": None, "reviewed_by_user_id": None, "reviewed_at": None,
        })
    return {
        "status": "no_documents" if not documents else "ready" if documents_with_ocr else "no_ocr_results",
        "method": "indexed_deterministic_candidates_v2",
        "summary": {
            "documents_total": len(documents), "documents_with_ocr": documents_with_ocr or 0,
            "documents_pending": sum(doc.processing_status in {"queued", "processing", "relationship_extraction_pending", "relationship_extraction_processing"} for doc in documents),
            "documents_with_processing_errors": sum(doc.processing_status in {"failed", "relationship_extraction_failed"} for doc in documents),
            "identifier_mentions": sum(len(item.get("identifiers", [])) for item in comparison_facts_by_document.values()),
            "candidate_entities": len(candidates),
            "candidate_links": sum(item["review_status"] in {"candidate", "pending"} for item in links),
            # An indexed decision remains authoritative for review_status even
            # when conflicting legacy records make the fallback mapping
            # ambiguous. Count that diagnostic independently of projection.
            "ambiguous_review_links": sum(
                (item.get("review_mapping") or {}).get("status") == "ambiguous"
                for item in links
            ),
            "total_links": len(links),
            "deterministic_candidate_links": sum(link.get("candidate_key") is not None for link in links),
            "confirmed_links": sum(item["review_status"] == "confirmed" for item in links),
            "rejected_links": sum(item["review_status"] == "rejected" for item in links),
            "unreviewed_links": sum(item["review_status"] in {"candidate", "pending"} for item in links),
            "contradictions": len(contradictions),
        },
        "links": links, "comparisons": comparisons, "contradictions": contradictions,
        "limitations": "Exact labeled identifiers are candidate links, not identity or transaction proof. Dates and amounts are comparison signals only. OCR uncertainty and unlabeled fields can affect results.",
        "semantic_enrichment": {"status": "pending" if pending else "partial" if failed else "ready"},
        "timings_ms": {"cache_hit": True, "deterministic_candidate_generation": round((perf_counter() - started) * 1000, 3)},
    }


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
        engine_section = await run_engine_pipeline(db, run, document)
        if engine_section.get("check_type") == "trufor_engine":
            result.setdefault("checks", []).append(engine_section)
            result["engine"] = engine_section.get("engine_health")
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


def _region_dict(region: VisualCandidateRegion) -> dict:
    return {
        "id": str(region.id),
        "region_index": region.region_index,
        "bbox_pixel": region.bbox_pixel,
        "bbox_pdf_points": region.bbox_pdf_points,
        "area_px": region.area_px,
        "mean_localization_score": region.mean_localization_score,
        "max_localization_score": region.max_localization_score,
        "mean_reliability_value": region.mean_reliability_value,
        "threshold_config": region.threshold_config,
        "postprocess_version": region.postprocess_version,
        "linked_ocr_block_ids": region.linked_ocr_block_ids or [],
        "review_status": region.review_status,
        "review_notes": region.review_notes,
        "reviewed_at": region.reviewed_at.isoformat() if region.reviewed_at else None,
    }


def _visual_page_dict(page: VisualAnalysisPage, regions: list[VisualCandidateRegion]) -> dict:
    return {
        "id": str(page.id),
        "run_id": str(page.run_id),
        "page_number": page.page_number,
        "status": page.status,
        "engine": page.engine,
        "engine_version": page.engine_version,
        "checkpoint_sha256": page.checkpoint_sha256,
        "device": page.device,
        "source_page_width_points": page.source_page_width_points,
        "source_page_height_points": page.source_page_height_points,
        "render_width_px": page.render_width_px,
        "render_height_px": page.render_height_px,
        "render_dpi": page.render_dpi,
        "page_rotation": page.page_rotation,
        "transform": page.transform_json or {},
        "integrity_score": page.integrity_score,
        "localization_artifact_id": str(page.localization_artifact_id) if page.localization_artifact_id else None,
        "reliability_artifact_id": str(page.reliability_artifact_id) if page.reliability_artifact_id else None,
        "noiseprint_artifact_id": str(page.noiseprint_artifact_id) if page.noiseprint_artifact_id else None,
        "diagnostics": page.diagnostics_json or {},
        "regions": [_region_dict(region) for region in regions],
    }


@router.get("/{investigation_id}/visual-analysis-runs/{run_id}/pages")
async def get_visual_analysis_run_pages(investigation_id: UUID, run_id: UUID, db: AsyncSession = Depends(get_db), user: User = Depends(require_permission("investigations:read"))):
    await owned_investigation(db, user, investigation_id)
    run = await db.scalar(select(VisualAnalysisRun).where(VisualAnalysisRun.id == run_id, VisualAnalysisRun.investigation_id == investigation_id))
    if run is None:
        raise HTTPException(status_code=404, detail="Visual analysis run not found")
    pages = (await db.scalars(select(VisualAnalysisPage).where(VisualAnalysisPage.run_id == run_id).order_by(VisualAnalysisPage.page_number))).all()
    if not pages:
        return {"run_id": str(run_id), "items": []}
    regions = (await db.scalars(select(VisualCandidateRegion).where(VisualCandidateRegion.page_id.in_([page.id for page in pages])))).all()
    regions_by_page: dict = {}
    for region in regions:
        regions_by_page.setdefault(str(region.page_id), []).append(region)
    return {"run_id": str(run_id), "items": [_visual_page_dict(page, sorted(regions_by_page.get(str(page.id), []), key=lambda item: item.region_index)) for page in pages]}


@router.patch("/{investigation_id}/visual-candidate-regions/{region_id}")
async def review_visual_candidate_region(investigation_id: UUID, region_id: UUID, payload: VisualRegionReview, request: Request, db: AsyncSession = Depends(get_db), user: User = Depends(require_permission("investigations:update"))):
    await owned_investigation(db, user, investigation_id)
    require_csrf(request)
    if payload.status not in REVIEW_STATUSES:
        raise HTTPException(status_code=422, detail="Invalid visual region review status")
    row = await db.execute(select(VisualCandidateRegion, VisualAnalysisPage, VisualAnalysisRun).join(VisualAnalysisPage, VisualAnalysisPage.id == VisualCandidateRegion.page_id).join(VisualAnalysisRun, VisualAnalysisRun.id == VisualAnalysisPage.run_id).where(VisualCandidateRegion.id == region_id, VisualAnalysisRun.investigation_id == investigation_id))
    region, page, run = row.first() or (None, None, None)
    if region is None:
        raise HTTPException(status_code=404, detail="Visual candidate region not found")
    region.review_status = payload.status
    region.review_notes = payload.review_notes
    region.reviewed_by_user_id = user.id
    region.reviewed_at = datetime.now(timezone.utc)
    await append_audit(db, investigation_id=investigation_id, actor_user_id=user.id, action="visual_candidate_region.reviewed", target_type="visual_candidate_region", target_id=str(region.id), details={"status": region.review_status, "page_number": page.page_number, "run_id": str(run.id), "review_notes": region.review_notes})
    await db.commit()
    return _region_dict(region)


@router.get("/{investigation_id}/visual-analysis-artifacts/{artifact_id}")
async def get_visual_analysis_artifact(investigation_id: UUID, artifact_id: UUID, db: AsyncSession = Depends(get_db), user: User = Depends(require_permission("investigations:read"))):
    await owned_investigation(db, user, investigation_id)
    artifact = await db.scalar(select(VisualAnalysisArtifact).where(VisualAnalysisArtifact.id == artifact_id, VisualAnalysisArtifact.investigation_id == investigation_id))
    if artifact is None:
        raise HTTPException(status_code=404, detail="Visual analysis artifact not found")
    path = Path(settings.visual_forensics_artifact_dir) / f"{artifact.storage_key}.png"
    if not path.is_file():
        raise HTTPException(status_code=410, detail="Visual analysis artifact file is missing from private storage")
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    if digest.hexdigest() != artifact.sha256:
        raise HTTPException(status_code=409, detail="Stored visual analysis artifact hash does not match its recorded SHA-256")
    await append_audit(
        db,
        investigation_id=investigation_id,
        actor_user_id=user.id,
        action="visual_analysis.artifact_accessed",
        target_type="visual_analysis_artifact",
        target_id=str(artifact.id),
        details={"artifact_type": artifact.artifact_type, "sha256": artifact.sha256},
    )
    await db.commit()
    return FileResponse(path, media_type=artifact.mime_type, content_disposition_type="inline", headers={"X-Content-SHA256": artifact.sha256, "Cache-Control": "no-store"})


@router.get("/{investigation_id}/visual-forensics-engine")
async def get_visual_forensics_engine_health(investigation_id: UUID, db: AsyncSession = Depends(get_db), user: User = Depends(require_permission("investigations:read"))):
    await owned_investigation(db, user, investigation_id)
    return engine_health_payload()


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
        details={"method": "hybrid_deterministic_exact_v2", "documents_considered": result["documents_considered"]},
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
            resolution="case_link_match" if link["relation_type"] == "shared_case_link" else "exact_entity_candidate" if link["relation_type"] == "shared_canonical_entity" else "identifier_match",
            basis=link["explanation"],
        )
        db.add(node)
    node.review_status = payload.status
    node.reviewed_by_user_id = user.id
    node.reviewed_at = datetime.now(timezone.utc)
    node.review_reason = payload.reason.strip()
    if not node.review_reason:
        raise HTTPException(status_code=422, detail="Review reason must not be blank")
    candidate = None
    if link.get("candidate_key"):
        candidate = await db.scalar(select(RelationshipCandidate).where(
            RelationshipCandidate.investigation_id == investigation_id,
            RelationshipCandidate.candidate_key == link["candidate_key"],
        ))
    if candidate:
        history = list(candidate.review_history or [])
        history.append({
            "status": payload.status, "reason": node.review_reason,
            "reviewed_by_user_id": str(user.id), "reviewed_at": node.reviewed_at.isoformat(),
        })
        candidate.review_status = payload.status
        candidate.reviewed_by_user_id = user.id
        candidate.reviewed_at = node.reviewed_at
        candidate.review_reason = node.review_reason
        candidate.review_history = history
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
        "lifecycle_status": candidate.lifecycle_status if candidate else "legacy",
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


@router.post("/{investigation_id}/documents/{document_id}/pdf-forensics", status_code=201)
async def analyze_pdf_structure(
    investigation_id: UUID,
    document_id: UUID,
    request: Request,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_permission("investigations:update")),
):
    require_csrf(request)
    await owned_investigation(db, user, investigation_id)
    document = await db.scalar(select(Document).where(Document.id == document_id, Document.investigation_id == investigation_id))
    if document is None:
        raise HTTPException(status_code=404, detail="Document not found")
    if document.content_type != "application/pdf":
        raise HTTPException(status_code=415, detail="PDF structural analysis is available for PDF sources")
    if not document.sha256:
        raise HTTPException(status_code=409, detail="Source evidence has no recorded SHA-256")
    path = stored_document_path(document)
    run_id = uuid4()
    artifact_dir = _pdf_artifact_run_dir(run_id)
    try:
        result = await asyncio.to_thread(analyze_pdf_isolated, path, document.sha256, artifact_dir=artifact_dir)
    except ValueError as exc:
        shutil.rmtree(artifact_dir, ignore_errors=True)
        detail = str(exc)
        if "SHA-256" in detail:
            raise HTTPException(status_code=409, detail="Stored evidence hash does not match its recorded SHA-256") from exc
        raise HTTPException(status_code=422, detail=detail) from exc
    except TimeoutError as exc:
        shutil.rmtree(artifact_dir, ignore_errors=True)
        raise HTTPException(status_code=504, detail="PDF structural analysis exceeded its process timeout") from exc
    except Exception as exc:
        shutil.rmtree(artifact_dir, ignore_errors=True)
        raise HTTPException(status_code=422, detail="PDF structural analysis could not parse this source") from exc

    ocr_result = await db.scalar(select(OCRResult).where(OCRResult.document_id == document.id))
    if ocr_result and isinstance(ocr_result.blocks, list):
        pages_by_number = {}
        for block in ocr_result.blocks:
            if not isinstance(block, dict) or not isinstance(block.get("page_index"), int):
                continue
            page_number = block["page_index"] + 1
            geometry = block.get("page_geometry")
            if isinstance(geometry, dict):
                pages_by_number[page_number] = {"page_number": page_number, "width": geometry.get("render_width_px"), "height": geometry.get("render_height_px")}
        result["text_comparison"] = compare_pdf_native_text_to_ocr(result, list(pages_by_number.values()), ocr_result.blocks)
    else:
        result["text_comparison"] = {"schema_version": "native_text_ocr_comparison_v1", "status": "unavailable", "method": "validated_pixel_to_pdf_bbox_overlap_and_normalized_text_comparison", "discrepancies": [], "skipped_pages": [], "limitations": ["No OCR result was available when this immutable PDF analysis run was created."]}

    artifact_records = result.pop("artifacts", [])
    run = PdfForensicRun(
        id=run_id,
        investigation_id=investigation_id,
        document_id=document.id,
        source_sha256=document.sha256,
        status=result["status"],
        parser_name=result["parser"]["name"],
        parser_version=result["parser"]["version"],
        result=result,
        created_by_user_id=user.id,
    )
    db.add(run)
    try:
        await db.flush()
        for artifact in artifact_records:
            db.add(PdfForensicArtifact(
                id=UUID(artifact["id"]), investigation_id=investigation_id,
                document_id=document.id, run_id=run.id, object_number=artifact["object_number"],
                source_sha256=document.sha256, storage_key=artifact["storage_key"],
                mime_type=artifact["mime_type"], byte_size=artifact["byte_size"],
                width_px=artifact["width_px"], height_px=artifact["height_px"], sha256=artifact["sha256"],
            ))
        await append_audit(
            db,
            investigation_id=investigation_id,
            actor_user_id=user.id,
            action="evidence.pdf_structure_analyzed",
            target_type="pdf_forensic_run",
            target_id=str(run.id),
            details={"document_id": str(document.id), "source_sha256": document.sha256, "status": run.status, "page_count": result["document"]["page_count"], "extracted_image_count": len(artifact_records)},
        )
        await db.commit()
        await db.refresh(run)
    except Exception:
        await db.rollback()
        shutil.rmtree(artifact_dir, ignore_errors=True)
        raise
    return _pdf_forensic_run_payload(run)


@router.get("/{investigation_id}/documents/{document_id}/pdf-forensics/latest")
async def latest_pdf_structure(
    investigation_id: UUID,
    document_id: UUID,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_permission("investigations:read")),
):
    await owned_investigation(db, user, investigation_id)
    document = await db.scalar(select(Document).where(Document.id == document_id, Document.investigation_id == investigation_id))
    if document is None:
        raise HTTPException(status_code=404, detail="Document not found")
    run = await db.scalar(
        select(PdfForensicRun)
        .where(PdfForensicRun.document_id == document.id, PdfForensicRun.investigation_id == investigation_id)
        .order_by(PdfForensicRun.created_at.desc(), PdfForensicRun.id.desc())
    )
    return {"item": _pdf_forensic_run_payload(run) if run else None}


@router.get("/{investigation_id}/pdf-forensics/summary")
async def pdf_structure_summary(
    investigation_id: UUID,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_permission("investigations:read")),
):
    await owned_investigation(db, user, investigation_id)
    pdf_documents = (await db.scalars(select(Document).where(
        Document.investigation_id == investigation_id,
        Document.content_type == "application/pdf",
    ))).all()
    rows = (await db.execute(
        select(PdfForensicRun, Document)
        .join(Document, Document.id == PdfForensicRun.document_id)
        .where(PdfForensicRun.investigation_id == investigation_id)
        .order_by(PdfForensicRun.created_at.desc(), PdfForensicRun.id.desc())
    )).all()
    latest_by_document = {}
    for run, document in rows:
        latest_by_document.setdefault(str(document.id), (run, document))
    latest = list(latest_by_document.values())
    pages_analyzed = sum(int((run.result.get("document") or {}).get("page_count") or 0) for run, _document in latest)
    return {
        "summary": {
            "pdf_documents": len(pdf_documents),
            "documents_analyzed": len(latest),
            "pages_analyzed": pages_analyzed,
            "completed": sum(run.status == "completed" for run, _document in latest),
            "partial": sum(run.status == "partial" for run, _document in latest),
        },
        "documents": [{
            "document_id": str(document.id), "filename": document.filename,
            "source_sha256": run.source_sha256, "status": run.status,
            "page_count": (run.result.get("document") or {}).get("page_count", 0),
            "created_at": run.created_at.isoformat(),
        } for run, document in latest],
    }


@router.get("/{investigation_id}/cross-method-correlations")
async def cross_method_correlations(
    investigation_id: UUID,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_permission("investigations:read")),
):
    await owned_investigation(db, user, investigation_id)
    pdf_rows = (await db.scalars(select(PdfForensicRun).where(PdfForensicRun.investigation_id == investigation_id).order_by(PdfForensicRun.created_at, PdfForensicRun.id))).all()
    visual_rows = (await db.scalars(select(VisualAnalysisRun).where(VisualAnalysisRun.investigation_id == investigation_id).order_by(VisualAnalysisRun.created_at, VisualAnalysisRun.id))).all()
    pdf_runs = [{"id": str(run.id), "document_id": str(run.document_id), "source_sha256": run.source_sha256, "result": run.result, "created_at": run.created_at.isoformat()} for run in pdf_rows]
    visual_runs = [{"id": str(run.id), "document_id": str(run.document_id), "result": run.result, "created_at": run.created_at.isoformat()} for run in visual_rows]
    return correlate_pdf_text_and_visual(pdf_runs, visual_runs)


@router.get("/{investigation_id}/pdf-forensic-artifacts/{artifact_id}")
async def get_pdf_forensic_artifact(
    investigation_id: UUID,
    artifact_id: UUID,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_permission("investigations:read")),
):
    await owned_investigation(db, user, investigation_id)
    artifact = await db.scalar(select(PdfForensicArtifact).where(
        PdfForensicArtifact.id == artifact_id,
        PdfForensicArtifact.investigation_id == investigation_id,
    ))
    if artifact is None:
        raise HTTPException(status_code=404, detail="PDF forensic artifact not found")
    run_dir = _pdf_artifact_run_dir(artifact.run_id)
    candidate = run_dir / artifact.storage_key
    path = candidate.resolve()
    if candidate.is_symlink() or path.parent != run_dir or not path.is_file():
        raise HTTPException(status_code=404, detail="PDF forensic artifact is unavailable")
    digest = await asyncio.to_thread(_sha256_file, path)
    if digest != artifact.sha256:
        raise HTTPException(status_code=409, detail="Stored artifact hash does not match its recorded SHA-256")
    await append_audit(
        db,
        investigation_id=investigation_id,
        actor_user_id=user.id,
        action="evidence.pdf_artifact_accessed",
        target_type="pdf_forensic_artifact",
        target_id=str(artifact.id),
        details={"document_id": str(artifact.document_id), "object_number": artifact.object_number, "sha256": digest},
    )
    await db.commit()
    return FileResponse(path, media_type=artifact.mime_type, filename=f"pdf-object-{artifact.object_number}.{artifact.mime_type.split('/')[-1]}", headers={"X-Content-SHA256": digest, "Cache-Control": "no-store"})


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
            "status": "unavailable" if not settings.llm_api_key else "not_used",
            "reason": "Groq is not configured." if not settings.llm_api_key else "Search returns stored evidence only. A model narrative is not generated for this request.",
        },
        "conflicts": [ _finding_dict(item) for item in findings if item.finding_type == "identifier_amount_conflict" and needle in f"{item.title} {item.description}".casefold() ],
    }


@router.get("/{investigation_id}/derived-nodes")
async def derived_nodes(investigation_id: UUID, db: AsyncSession = Depends(get_db), user: User = Depends(require_permission("investigations:read"))):
    await owned_investigation(db, user, investigation_id)
    rows = (await db.scalars(select(DerivedGraphNode).where(DerivedGraphNode.investigation_id == investigation_id))).all()
    return {"items": [{"id": item.canonical_id, "label": item.label, "type": item.entity_type, "document_ids": item.document_ids, "resolution": item.resolution, "basis": item.basis} for item in rows]}

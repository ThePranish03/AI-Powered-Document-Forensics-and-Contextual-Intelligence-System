from datetime import datetime, timezone
import uuid
from uuid import UUID

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.entities import (
    AuditChainEvent,
    DerivedGraphNode,
    Document,
    DossierSnapshot,
    ExtractionRun,
    Finding,
    OCRResult,
    PdfForensicRun,
    RelationshipAnalysis,
    RelationshipCandidate,
    TimelineEvent,
    VisualAnalysisRun,
    VisualAnalysisPage,
    VisualAnalysisArtifact,
    VisualCandidateRegion,
)
from app.services.audit_chain import append_audit, verify_audit_chain
from app.services.dossier_narrative import generate_grounded_narrative
from app.services.evidence_correlation import correlate_pdf_text_and_visual
from app.services.dossier_report import build_snapshot, render_html, snapshot_digest
from app.services.ocr_metrics import analyze_documents, build_cross_document_correlation, extract_document_facts
from app.services.relationship_index import _candidate_key
from app.services.relationship_review import consistent_legacy_review_field, project_candidate_review, resolve_legacy_candidate_review, review_history_status

ANALYZER_NAME = "deterministic_evidence"
ANALYZER_VERSION = "1"


def _finding_dict(item: Finding) -> dict:
    return {
        "id": str(item.id),
        "finding_type": item.finding_type,
        "title": item.title,
        "description": item.description,
        "severity": item.severity,
        "confidence": item.confidence,
        "status": item.status,
        "analyzer_name": item.analyzer_name,
        "analyzer_version": item.analyzer_version,
        "method": item.method,
        "evidence_refs": item.evidence_refs,
        "limitations": item.limitations,
        "review_notes": item.review_notes,
        "reviewed_by_user_id": str(item.reviewed_by_user_id) if item.reviewed_by_user_id else None,
        "reviewed_at": item.reviewed_at.isoformat() if item.reviewed_at else None,
        "created_at": item.created_at.isoformat(),
    }


def _timeline_dict(item: TimelineEvent) -> dict:
    return {
        "id": str(item.id),
        "raw_date": item.raw_date,
        "interpreted_date": item.interpreted_date,
        "ambiguous": item.ambiguous,
        "timezone_known": item.timezone_known,
        "event_type": item.event_type,
        "description": item.description,
        "confidence": item.confidence,
        "evidence_refs": item.evidence_refs,
        "annotation": item.annotation,
        "created_at": item.created_at.isoformat(),
    }


async def run_investigation_analysis(db: AsyncSession, investigation, actor_id: UUID) -> dict:
    rows = (await db.execute(
        select(Document, OCRResult, RelationshipAnalysis)
        .outerjoin(OCRResult, OCRResult.document_id == Document.id)
        .outerjoin(RelationshipAnalysis, RelationshipAnalysis.document_id == Document.id)
        .where(Document.investigation_id == investigation.id)
    )).all()
    payloads = []
    for document, result, structured in rows:
        payloads.append({
            "document_id": str(document.id),
            "filename": document.filename,
            "sha256": document.sha256,
            "provider": result.provider if result else None,
            "provider_version": result.provider_version if result else None,
            "blocks": result.blocks if result else [],
            "text": result.recognized_text if result else "",
            "processing_status": document.processing_status,
            "structured_extraction": structured.payload if structured else None,
        })
    produced = analyze_documents(payloads)
    kept = (await db.scalars(select(Finding).where(
        Finding.investigation_id == investigation.id,
        Finding.analyzer_name == ANALYZER_NAME,
        Finding.status != "candidate",
    ))).all()
    kept_fingerprints = {item.fingerprint for item in kept}
    await db.execute(delete(Finding).where(
        Finding.investigation_id == investigation.id,
        Finding.analyzer_name == ANALYZER_NAME,
        Finding.status == "candidate",
    ))
    created = 0
    for item in produced["findings"]:
        if item["fingerprint"] in kept_fingerprints:
            continue
        db.add(Finding(
            investigation_id=investigation.id,
            finding_type=item["finding_type"],
            title=item["title"],
            description=item["description"],
            severity=item["severity"],
            confidence=item["confidence"],
            status="candidate",
            analyzer_name=ANALYZER_NAME,
            analyzer_version=ANALYZER_VERSION,
            method=item["method"],
            fingerprint=item["fingerprint"],
            evidence_refs=item["evidence_refs"],
            limitations=item["limitations"],
        ))
        created += 1
    await db.execute(delete(TimelineEvent).where(
        TimelineEvent.investigation_id == investigation.id,
        TimelineEvent.annotation.is_(None),
    ))
    for item in produced["timeline"]:
        db.add(TimelineEvent(
            investigation_id=investigation.id,
            raw_date=item["raw_date"],
            interpreted_date=item["interpreted_date"],
            ambiguous=item["ambiguous"],
            timezone_known=False,
            event_type=item["event_type"],
            description=item["description"],
            confidence=item["confidence"],
            evidence_refs=item["evidence_refs"],
        ))
    existing_nodes = {
        item.canonical_id: item
        for item in (await db.scalars(select(DerivedGraphNode).where(DerivedGraphNode.investigation_id == investigation.id))).all()
    }
    current_node_ids = {node["canonical_id"] for node in produced["nodes"]}
    for canonical_id, item in existing_nodes.items():
        if canonical_id not in current_node_ids and item.review_status == "candidate":
            await db.delete(item)
    for node in produced["nodes"]:
        item = existing_nodes.get(node["canonical_id"])
        if item is None:
            item = DerivedGraphNode(
                investigation_id=investigation.id,
                canonical_id=node["canonical_id"],
                label=node["label"],
                entity_type=node["entity_type"],
                document_ids=list(dict.fromkeys(node["document_ids"])),
                resolution=node["resolution"],
                basis=node["basis"],
            )
            db.add(item)
        else:
            item.label = node["label"]
            item.entity_type = node["entity_type"]
            item.document_ids = list(dict.fromkeys(node["document_ids"]))
            item.resolution = node["resolution"]
            item.basis = node["basis"]
    await append_audit(
        db,
        investigation_id=investigation.id,
        actor_user_id=actor_id,
        action="analysis.completed",
        target_type="investigation",
        target_id=str(investigation.id),
        details={"analyzer": ANALYZER_NAME, "analyzer_version": ANALYZER_VERSION, "candidate_findings_written": created, "documents_considered": len(payloads)},
    )
    await db.commit()
    visual_runs = (await db.scalars(select(VisualAnalysisRun).where(VisualAnalysisRun.investigation_id == investigation.id).order_by(VisualAnalysisRun.created_at.desc()))).all()
    return {
        "analyzer": ANALYZER_NAME,
        "analyzer_version": ANALYZER_VERSION,
        "documents_considered": len(payloads),
        "documents_with_ocr": sum(1 for item in payloads if item["text"] or item["blocks"]),
        "candidate_findings_written": created,
        "diagnostics": produced["diagnostics"],
        "timeline_events_written": len(produced["timeline"]),
        "graph_nodes_written": len(produced["nodes"]),
        "visual_forensics": {
            "status": "completed" if any(run.status == "completed" for run in visual_runs) else ("partial" if visual_runs else "not_run"),
            "runs": [{"id": str(run.id), "document_id": str(run.document_id), "status": run.status, "observations_written": (run.result or {}).get("observations_written", 0)} for run in visual_runs],
            "checks": [check for run in visual_runs for check in (run.result or {}).get("checks", [])],
            "available_signals": [],
            "related_non_visual_signals": ["duplicate file bytes", "empty OCR text", "low engine-confidence regions"],
            "reason": "Visual analysis runs on stored source bytes when requested for a document. No run means no visual result.",
        },
    }


async def create_dossier(db: AsyncSession, investigation, actor_id: UUID) -> DossierSnapshot:
    document_rows = (await db.execute(
        select(Document, OCRResult)
        .outerjoin(OCRResult, OCRResult.document_id == Document.id)
        .where(Document.investigation_id == investigation.id)
        .order_by(Document.created_at, Document.id)
    )).all()
    documents = [document for document, _result in document_rows]
    extraction_runs = (await db.scalars(
        select(ExtractionRun).join(Document, Document.id == ExtractionRun.document_id)
        .where(Document.investigation_id == investigation.id)
        .order_by(ExtractionRun.document_id, ExtractionRun.run_number)
    )).all()
    runs_by_document: dict[str, list[dict]] = {}
    for run in extraction_runs:
        runs_by_document.setdefault(str(run.document_id), []).append({
            "id": str(run.id), "run_number": run.run_number, "provider": run.provider,
            "provider_version": run.provider_version, "status": run.status,
            "started_at": run.started_at.isoformat() if run.started_at else None,
            "completed_at": run.completed_at.isoformat() if run.completed_at else None,
            "duration_ms": run.latency_ms, "error_detail": run.error_detail,
            "configuration": run.configuration,
        })
    findings = (await db.scalars(select(Finding).where(Finding.investigation_id == investigation.id))).all()
    timeline = (await db.scalars(select(TimelineEvent).where(TimelineEvent.investigation_id == investigation.id))).all()
    nodes = (await db.scalars(select(DerivedGraphNode).where(DerivedGraphNode.investigation_id == investigation.id))).all()
    relationship_rows = (await db.execute(
        select(RelationshipAnalysis, Document)
        .join(Document, Document.id == RelationshipAnalysis.document_id)
        .where(Document.investigation_id == investigation.id)
        .order_by(Document.created_at, Document.id)
    )).all()
    audit_events = (await db.scalars(
        select(AuditChainEvent)
        .where(AuditChainEvent.investigation_id == investigation.id)
        .order_by(AuditChainEvent.created_at, AuditChainEvent.id)
    )).all()
    visual_runs = (await db.scalars(select(VisualAnalysisRun).where(VisualAnalysisRun.investigation_id == investigation.id).order_by(VisualAnalysisRun.created_at, VisualAnalysisRun.id))).all()
    pdf_runs = (await db.scalars(select(PdfForensicRun).where(PdfForensicRun.investigation_id == investigation.id).order_by(PdfForensicRun.created_at, PdfForensicRun.id))).all()
    pdf_run_snapshot = [{
        "id": str(run.id), "document_id": str(run.document_id), "source_sha256": run.source_sha256,
        "status": run.status, "parser": {"name": run.parser_name, "version": run.parser_version},
        "result": run.result, "created_at": run.created_at.isoformat(),
    } for run in pdf_runs]
    visual_run_snapshot = []
    document_by_id = {str(document.id): document for document, _ in document_rows}
    for run in visual_runs:
        pages = (await db.scalars(select(VisualAnalysisPage).where(VisualAnalysisPage.run_id == run.id).order_by(VisualAnalysisPage.page_number))).all()
        page_items = []
        for page in pages:
            regions = (await db.scalars(select(VisualCandidateRegion).where(VisualCandidateRegion.page_id == page.id).order_by(VisualCandidateRegion.region_index))).all()
            artifacts = (await db.scalars(select(VisualAnalysisArtifact).where(VisualAnalysisArtifact.page_id == page.id))).all()
            page_items.append({
                "page_number": page.page_number, "status": page.status, "engine": page.engine,
                "engine_version": page.engine_version, "checkpoint_sha256": page.checkpoint_sha256,
                "integrity_score": page.integrity_score, "integrity_meaning": "Model output as defined by the recorded engine; not a calibrated fraud probability or authenticity verdict.",
                "transform": page.transform_json, "diagnostics": page.diagnostics_json,
                "artifacts": [{"id": str(item.id), "type": item.artifact_type, "sha256": item.sha256, "width_px": item.width_px, "height_px": item.height_px, "mime_type": item.mime_type} for item in artifacts],
                "localization_artifact_id": str(page.localization_artifact_id) if page.localization_artifact_id else None,
                "reliability_artifact_id": str(page.reliability_artifact_id) if page.reliability_artifact_id else None,
                "regions": [{"id": str(region.id), "region_index": region.region_index, "bbox_pixel": region.bbox_pixel, "bbox_pdf_points": region.bbox_pdf_points, "mean_localization_score": region.mean_localization_score, "max_localization_score": region.max_localization_score, "mean_reliability_value": region.mean_reliability_value, "linked_ocr_block_ids": region.linked_ocr_block_ids, "review_status": region.review_status, "review_notes": region.review_notes, "reviewed_by_user_id": str(region.reviewed_by_user_id) if region.reviewed_by_user_id else None, "reviewed_at": region.reviewed_at.isoformat() if region.reviewed_at else None} for region in regions],
            })
        document = document_by_id.get(str(run.document_id))
        visual_run_snapshot.append({"id": str(run.id), "document_id": str(run.document_id), "source_sha256": document.sha256 if document else None, "status": run.status, "analyzer_version": run.analyzer_version, "model_version": (run.result or {}).get("engine", {}).get("model_version") if isinstance((run.result or {}).get("engine"), dict) else None, "result": run.result, "pages": page_items, "created_at": run.created_at.isoformat(), "completed_at": run.completed_at.isoformat() if run.completed_at else None, "error_detail": run.error_detail})
    version = (await db.scalar(select(func.max(DossierSnapshot.version)).where(DossierSnapshot.investigation_id == investigation.id)) or 0) + 1
    structured_by_document = {str(document.id): record.payload for record, document in relationship_rows}
    correlation = build_cross_document_correlation([{
        "document_id": str(document.id),
        "filename": document.filename,
        "sha256": document.sha256,
        "provider": result.provider if result else None,
        "provider_version": result.provider_version if result else None,
        "blocks": result.blocks if result else [],
        "text": result.recognized_text if result else "",
        "processing_status": document.processing_status,
        "structured_extraction": structured_by_document.get(str(document.id)),
    } for document, result in document_rows])
    deterministic_analysis = analyze_documents([{
        "document_id": str(document.id), "filename": document.filename,
        "sha256": document.sha256, "provider": result.provider if result else None,
        "provider_version": result.provider_version if result else None,
        "blocks": result.blocks if result else [], "text": result.recognized_text if result else "",
        "processing_status": document.processing_status,
        "structured_extraction": structured_by_document.get(str(document.id)),
    } for document, result in document_rows])
    fact_by_document = {
        fact["document_id"]: fact
        for fact in (extract_document_facts({
            "document_id": str(document.id), "filename": document.filename,
            "sha256": document.sha256, "provider": result.provider if result else None,
            "provider_version": result.provider_version if result else None,
            "blocks": result.blocks if result else [], "text": result.recognized_text if result else "",
            "processing_status": document.processing_status,
            "structured_extraction": structured_by_document.get(str(document.id)),
        }) for document, result in document_rows)
    }
    candidates_by_key = {
        item.candidate_key: item
        for item in (await db.scalars(select(RelationshipCandidate).where(
            RelationshipCandidate.investigation_id == investigation.id,
        ))).all()
    }
    legacy_nodes_by_candidate_key = {}
    for node in nodes:
        key = _candidate_key(investigation.id, node.entity_type, node.label)
        legacy_nodes_by_candidate_key.setdefault(key, []).append(node)
    legacy_review_mappings = {}
    for key, group in legacy_nodes_by_candidate_key.items():
        candidate = candidates_by_key.get(key)
        source_documents = (
            candidate.document_ids if candidate is not None
            else sorted({str(value) for node in group for value in (node.document_ids or [])})
        )
        legacy_review_mappings[key] = resolve_legacy_candidate_review(
            investigation_id=investigation.id,
            evidence_type=group[0].entity_type,
            normalized_value=group[0].label,
            candidate_document_ids=source_documents,
            legacy_nodes=group,
        )
    for link in correlation["links"]:
        candidate_key = _candidate_key(
            investigation.id, link["identifier_type"], link["normalized_identifier"],
        )
        candidate = candidates_by_key.get(candidate_key)
        legacy_review = legacy_review_mappings.get(candidate_key, {
            "status": "unmatched", "reason": "no_exact_legacy_identity",
            "review_status": None, "legacy_node_ids": [], "decisions": [],
            "review_metadata": [], "review_history_available": False,
            "review_history_limitation": None,
        })
        indexed_review_status = candidate.review_status if candidate is not None else None
        projected_review = project_candidate_review(indexed_review_status, legacy_review)
        if candidate is not None:
            link["id"] = "detrel:" + candidate.candidate_key[:24]
            link["candidate_key"] = candidate.candidate_key
            link["lifecycle_status"] = candidate.lifecycle_status
            link["review_history"] = candidate.review_history or []
        if candidate is not None and candidate.review_status != "pending":
            link["review_status"] = projected_review["review_status"]
            link["review_reason"] = candidate.review_reason
            link["reviewed_at"] = candidate.reviewed_at.isoformat() if candidate.reviewed_at else None
            link["reviewed_by_user_id"] = str(candidate.reviewed_by_user_id) if candidate.reviewed_by_user_id else None
            link["review_source"] = projected_review["review_source"]
        elif legacy_review["status"] == "matched":
            link["review_status"] = projected_review["review_status"]
            link["review_reason"] = consistent_legacy_review_field(legacy_review, "review_reason")
            reviewed_at = consistent_legacy_review_field(legacy_review, "reviewed_at")
            reviewed_by = consistent_legacy_review_field(legacy_review, "reviewed_by_user_id")
            link["reviewed_at"] = reviewed_at.isoformat() if reviewed_at else None
            link["reviewed_by_user_id"] = reviewed_by
            link["review_source"] = projected_review["review_source"]
        elif legacy_review["status"] == "ambiguous":
            link["review_status"] = projected_review["review_status"]
            link["review_source"] = projected_review["review_source"]
            link["review_reason"] = None
            link["reviewed_at"] = None
            link["reviewed_by_user_id"] = None
        else:
            link["review_source"] = None
        link["indexed_review_status"] = indexed_review_status
        link["review_mapping"] = legacy_review
        if candidate is None:
            link["review_history"] = []
        link["review_history_status"] = review_history_status(link.get("review_history"), legacy_review)
    dossier_nodes = []
    for node in nodes:
        key = _candidate_key(investigation.id, node.entity_type, node.label)
        candidate = candidates_by_key.get(key)
        mapping = legacy_review_mappings[key]
        indexed_status = candidate.review_status if candidate is not None else None
        projected = project_candidate_review(indexed_status, mapping)
        if candidate is not None and candidate.review_status != "pending":
            reviewed_by = candidate.reviewed_by_user_id
            reviewed_at = candidate.reviewed_at
            review_reason = candidate.review_reason
        elif mapping["status"] == "matched":
            reviewed_by = consistent_legacy_review_field(mapping, "reviewed_by_user_id")
            reviewed_at = consistent_legacy_review_field(mapping, "reviewed_at")
            review_reason = consistent_legacy_review_field(mapping, "review_reason")
        else:
            reviewed_by = reviewed_at = review_reason = None
        history = candidate.review_history if candidate is not None else []
        dossier_nodes.append({
            "id": node.canonical_id, "label": node.label, "type": node.entity_type,
            "document_ids": node.document_ids, "resolution": node.resolution, "basis": node.basis,
            "review_status": projected["review_status"], "review_source": projected["review_source"],
            "indexed_review_status": indexed_status,
            "review_reason": review_reason,
            "reviewed_at": reviewed_at.isoformat() if reviewed_at else None,
            "reviewed_by_user_id": str(reviewed_by) if reviewed_by else None,
            "review_mapping": mapping, "review_history": history,
            "review_history_status": review_history_status(history, mapping),
        })
    snapshot = build_snapshot(
        investigation={
            "id": str(investigation.id),
            "case_number": investigation.case_number,
            "title": investigation.title,
            "description": investigation.description,
            "status": investigation.status.value,
            "created_at": investigation.created_at.isoformat(),
            "updated_at": investigation.updated_at.isoformat(),
        },
        documents=[{
            "id": str(item.id),
            "filename": item.filename,
            "sha256": item.sha256,
            "content_type": item.content_type,
            "size_bytes": item.size_bytes,
            "processing_status": item.processing_status,
            "processing_error": item.processing_error,
            "processing_error_code": item.processing_error_code,
            "processing_error_retryable": item.processing_error_retryable,
            "processing_attempts": item.processing_attempts,
            "relationship_extraction_attempts": item.relationship_extraction_attempts,
            "relationship_extraction_status": (
                "completed" if item.processing_status == "relationship_extraction_completed" else
                "failed" if item.processing_status == "relationship_extraction_failed" else
                "processing" if item.processing_status == "relationship_extraction_processing" else
                "pending" if item.processing_status == "relationship_extraction_pending" else
                "not_run"
            ),
            "source_description": item.source_description,
            "uploaded_at": item.created_at.isoformat(),
            "extracted_text": (result.recognized_text if result else ""),
            "extracted_blocks": (result.blocks if result else []),
            "extraction_coverage": ((result.raw_result or {}).get("extraction_coverage") if result and isinstance(result.raw_result, dict) else None),
            "extraction_runs": runs_by_document.get(str(item.id), []),
            "structured_fields": fact_by_document.get(str(item.id), {}).get("structured_fields", []),
            "table_rows": fact_by_document.get(str(item.id), {}).get("table_rows", []),
        } for item, result in document_rows],
        findings=[_finding_dict(item) for item in findings],
        timeline=[_timeline_dict(item) for item in timeline],
        nodes=dossier_nodes,
        correlations=correlation["links"],
        discrepancies=correlation["contradictions"],
        audit_activity=[{
            "id": str(item.id), "action": item.action, "target_type": item.target_type,
            "target_id": item.target_id, "actor_user_id": str(item.actor_user_id) if item.actor_user_id else None,
            "created_at": item.created_at.isoformat(), "details": item.details,
            "previous_hash": item.previous_hash, "event_hash": item.event_hash,
        } for item in audit_events],
        document_relationships=[{"document_id": str(document.id), "filename": document.filename, "payload": record.payload, "model": record.model, "schema_version": record.schema_version, "created_at": record.created_at.isoformat()} for record, document in relationship_rows],
        audit_verification=verify_audit_chain(audit_events),
        visual_runs=visual_run_snapshot,
        pdf_runs=pdf_run_snapshot,
    )
    source_blocks = {
        (str(document.id), str(block.get("block_id"))): block
        for document, result in document_rows
        for block in (result.blocks if result and isinstance(result.blocks, list) else [])
        if isinstance(block, dict) and block.get("block_id")
    }
    def enrich_references(records: list[dict]) -> list[dict]:
        for record in records:
            for ref in record.get("evidence_refs", []):
                block = source_blocks.get((str(ref.get("document_id", "")), str(ref.get("block_id", ""))))
                if block:
                    ref.setdefault("bounding_box", block.get("engine_bbox", block.get("bounding_box")))
                    ref.setdefault("engine_confidence", block.get("engine_confidence"))
        return records
    snapshot["findings"] = enrich_references(snapshot["findings"])
    snapshot["reviewed_findings"] = [item for item in snapshot["findings"] if item["status"] != "candidate"]
    snapshot["timeline"] = enrich_references(snapshot["timeline"])
    snapshot["cross_document_links"] = enrich_references(snapshot["cross_document_links"])
    snapshot["cross_document_discrepancies"] = enrich_references(snapshot["cross_document_discrepancies"])
    snapshot["cross_method_correlations"] = correlate_pdf_text_and_visual(pdf_run_snapshot, visual_run_snapshot)
    snapshot["findings_diagnostics"] = deterministic_analysis["diagnostics"]
    snapshot["ai_narrative"] = await generate_grounded_narrative(snapshot)
    report_id = uuid.uuid4()
    created_at = datetime.now(timezone.utc)
    snapshot["report"] = {
        "dossier_id": str(report_id), "version": version,
        "created_at": created_at.isoformat(), "created_by_user_id": str(actor_id),
        "integrity_algorithm": "sha256(snapshot-json + newline + html)",
    }
    record = DossierSnapshot(
        id=report_id,
        investigation_id=investigation.id,
        version=version,
        snapshot=snapshot,
        html=render_html(snapshot),
        content_sha256="pending",
        created_by_user_id=actor_id,
        created_at=created_at,
    )
    record.content_sha256 = snapshot_digest(record.snapshot, record.html)
    record.snapshot = {**record.snapshot, "report": {**record.snapshot["report"], "integrity_sha256": record.content_sha256}}
    db.add(record)
    await db.flush()
    dossier_event = await append_audit(
        db,
        investigation_id=investigation.id,
        actor_user_id=actor_id,
        action="dossier.created",
        target_type="dossier",
        target_id=str(record.id),
        details={"version": version, "timestamp": datetime.now(timezone.utc).isoformat(), "actor_user_id": str(actor_id), "investigation_id": str(investigation.id)},
    )
    record.snapshot = {
        **record.snapshot,
        "audit_activity": [*record.snapshot.get("audit_activity", []), {
            "id": str(dossier_event.id),
            "action": dossier_event.action,
            "target_type": dossier_event.target_type,
            "target_id": dossier_event.target_id,
            "actor_user_id": str(dossier_event.actor_user_id) if dossier_event.actor_user_id else None,
            "created_at": dossier_event.created_at.isoformat(),
            "details": dossier_event.details,
            "previous_hash": dossier_event.previous_hash,
            "event_hash": dossier_event.event_hash,
        }],
    }
    dossier_events = [*audit_events, dossier_event]
    record.snapshot["audit_chain_verification"] = verify_audit_chain(dossier_events)
    record.html = render_html(record.snapshot)
    record.content_sha256 = snapshot_digest(record.snapshot, record.html)
    record.snapshot = {**record.snapshot, "report": {**record.snapshot["report"], "integrity_sha256": record.content_sha256}}
    await db.commit()
    await db.refresh(record)
    return record

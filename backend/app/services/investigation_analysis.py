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
    Finding,
    OCRResult,
    RelationshipAnalysis,
    TimelineEvent,
    VisualAnalysisRun,
)
from app.services.audit_chain import append_audit, verify_audit_chain
from app.services.dossier_narrative import generate_grounded_narrative
from app.services.dossier_report import build_snapshot, render_html, snapshot_digest
from app.services.ocr_metrics import analyze_documents, build_cross_document_correlation

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
    version = (await db.scalar(select(func.max(DossierSnapshot.version)).where(DossierSnapshot.investigation_id == investigation.id)) or 0) + 1
    correlation = build_cross_document_correlation([{
        "document_id": str(document.id),
        "filename": document.filename,
        "sha256": document.sha256,
        "provider": result.provider if result else None,
        "provider_version": result.provider_version if result else None,
        "blocks": result.blocks if result else [],
        "text": result.recognized_text if result else "",
        "processing_status": document.processing_status,
    } for document, result in document_rows])
    nodes_by_id = {item.canonical_id: item for item in nodes}
    for link in correlation["links"]:
        reviewed = nodes_by_id.get(link["id"])
        if reviewed:
            link["review_status"] = reviewed.review_status
            link["review_reason"] = reviewed.review_reason
            link["reviewed_at"] = reviewed.reviewed_at.isoformat() if reviewed.reviewed_at else None
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
            "source_description": item.source_description,
            "uploaded_at": item.created_at.isoformat(),
            "extracted_text": (result.recognized_text if result else ""),
            "extracted_blocks": (result.blocks if result else []),
        } for item, result in document_rows],
        findings=[_finding_dict(item) for item in findings],
        timeline=[_timeline_dict(item) for item in timeline],
        nodes=[{
            "id": item.canonical_id,
            "label": item.label,
            "type": item.entity_type,
            "document_ids": item.document_ids,
            "resolution": item.resolution,
            "basis": item.basis,
            "review_status": item.review_status,
            "review_reason": item.review_reason,
            "reviewed_at": item.reviewed_at.isoformat() if item.reviewed_at else None,
        } for item in nodes],
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
        visual_runs=[{"id": str(run.id), "document_id": str(run.document_id), "status": run.status, "analyzer_version": run.analyzer_version, "result": run.result, "created_at": run.created_at.isoformat(), "completed_at": run.completed_at.isoformat() if run.completed_at else None} for run in visual_runs],
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

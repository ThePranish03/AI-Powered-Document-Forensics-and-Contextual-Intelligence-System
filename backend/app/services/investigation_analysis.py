from uuid import UUID

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.entities import (
    DerivedGraphNode,
    Document,
    DossierSnapshot,
    Finding,
    OCRResult,
    TimelineEvent,
)
from app.services.audit_chain import append_audit
from app.services.dossier_report import build_snapshot, render_html
from app.services.ocr_metrics import analyze_documents

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
        select(Document, OCRResult)
        .outerjoin(OCRResult, OCRResult.document_id == Document.id)
        .where(Document.investigation_id == investigation.id)
    )).all()
    payloads = []
    for document, result in rows:
        payloads.append({
            "document_id": str(document.id),
            "filename": document.filename,
            "sha256": document.sha256,
            "provider": result.provider if result else None,
            "provider_version": result.provider_version if result else None,
            "blocks": result.blocks if result else [],
            "text": result.recognized_text if result else "",
            "processing_status": document.processing_status,
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
    await db.execute(delete(DerivedGraphNode).where(DerivedGraphNode.investigation_id == investigation.id))
    for node in produced["nodes"]:
        db.add(DerivedGraphNode(
            investigation_id=investigation.id,
            canonical_id=node["canonical_id"],
            label=node["label"],
            entity_type=node["entity_type"],
            document_ids=list(dict.fromkeys(node["document_ids"])),
            resolution=node["resolution"],
            basis=node["basis"],
        ))
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
    return {
        "analyzer": ANALYZER_NAME,
        "analyzer_version": ANALYZER_VERSION,
        "documents_considered": len(payloads),
        "documents_with_ocr": sum(1 for item in payloads if item["text"] or item["blocks"]),
        "candidate_findings_written": created,
        "timeline_events_written": len(produced["timeline"]),
        "graph_nodes_written": len(produced["nodes"]),
        "visual_forensics": {
            "status": "unavailable",
            "available_signals": [],
            "related_non_visual_signals": ["duplicate file bytes", "empty OCR text", "low engine-confidence regions"],
            "unavailable": ["font inconsistency", "copy-move detection", "compression anomaly", "forgery verdict"],
            "reason": "Automated image-forensics checks are not implemented. Duplicate hashes and OCR quality are separate signals, not visual analysis.",
        },
    }


async def create_dossier(db: AsyncSession, investigation, actor_id: UUID) -> DossierSnapshot:
    documents = (await db.scalars(select(Document).where(Document.investigation_id == investigation.id))).all()
    findings = (await db.scalars(select(Finding).where(Finding.investigation_id == investigation.id))).all()
    timeline = (await db.scalars(select(TimelineEvent).where(TimelineEvent.investigation_id == investigation.id))).all()
    nodes = (await db.scalars(select(DerivedGraphNode).where(DerivedGraphNode.investigation_id == investigation.id))).all()
    version = (await db.scalar(select(func.max(DossierSnapshot.version)).where(DossierSnapshot.investigation_id == investigation.id)) or 0) + 1
    snapshot = build_snapshot(
        investigation={
            "id": str(investigation.id),
            "case_number": investigation.case_number,
            "title": investigation.title,
            "description": investigation.description,
            "status": investigation.status.value,
        },
        documents=[{
            "id": str(item.id),
            "filename": item.filename,
            "sha256": item.sha256,
            "content_type": item.content_type,
            "size_bytes": item.size_bytes,
            "processing_status": item.processing_status,
        } for item in documents],
        findings=[_finding_dict(item) for item in findings],
        timeline=[_timeline_dict(item) for item in timeline],
        nodes=[{
            "id": item.canonical_id,
            "label": item.label,
            "type": item.entity_type,
            "document_ids": item.document_ids,
            "resolution": item.resolution,
            "basis": item.basis,
        } for item in nodes],
    )
    record = DossierSnapshot(
        investigation_id=investigation.id,
        version=version,
        snapshot=snapshot,
        html=render_html(snapshot),
        created_by_user_id=actor_id,
    )
    db.add(record)
    await db.flush()
    await append_audit(
        db,
        investigation_id=investigation.id,
        actor_user_id=actor_id,
        action="dossier.created",
        target_type="dossier",
        target_id=str(record.id),
        details={"version": version},
    )
    await db.commit()
    await db.refresh(record)
    return record

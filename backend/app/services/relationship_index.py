"""Indexed, occurrence-preserving deterministic relationship evidence."""
from __future__ import annotations

import hashlib
import ipaddress
import re
import unicodedata
import uuid
from datetime import datetime, timezone

from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.entities import DeterministicEvidenceMention, DerivedGraphNode, Document, Investigation, RelationshipCandidate
from app.services.relationship_review import consistent_legacy_review_field, resolve_legacy_candidate_review

MATCHER_VERSION = "deterministic_mentions_v3"
_EMAIL = re.compile(r"(?i)(?<![\w.+-])[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}(?![\w.-])")
_INVOICE = re.compile(
    r"(?i)\b(?:invoice|inv)\s*(?:number|no|#|id)?\s*[:#-]?\s*"
    r"(?P<value>[A-Z0-9][A-Z0-9./_-]{2,62}-[ \t]+[A-Z](?![A-Z0-9./_-])|"
    r"[A-Z0-9][A-Z0-9./_-]{2,63})"
)
_RULES = (
    ("PHONE", re.compile(r"(?i)\b(?:phone|telephone|mobile|contact(?:\s+phone)?|tel)\b\s*[:#-]?\s*(?P<value>\+?\d[\d().\s-]{5,}\d)"), "labeled phone", "strong_identifier"),
    ("IP", re.compile(r"(?i)\b(?:source\s+ip|ip(?:v[46])?\s+address|ip)\b\s*[:#-]?\s*(?P<value>[0-9a-f:.]{7,39})"), "labeled IP", "strong_identifier"),
    ("REGISTRATION_OR_TAX_ID", re.compile(r"(?i)\b(?:company\s+)?(?:registration(?:\s*/\s*tax)?(?:\s+(?:id|no|number))?|tax\s+(?:id|number)|gstin|vat(?:\s+(?:id|number))?|ein|company\s+number)\b\s*[:#-]?\s*(?P<value>[A-Z0-9][A-Z0-9/-]{3,})"), "labeled registration/tax identifier", "strong_identifier"),
    ("BANK_ACCOUNT", re.compile(r"(?i)\b(?:bank\s+account(?:\s+(?:number|no|id))?|beneficiary\s+account|account\s+number|iban)\b\s*[:#-]?\s*(?P<value>[A-Z0-9][A-Z0-9 -]{5,33})"), "labeled bank account", "strong_identifier"),
    ("TRANSACTION", re.compile(r"(?i)\b(?:transaction\s+(?:id|reference)|transaction|txn|tx\s+id)\b\s*[:#-]?\s*(?P<value>[A-Z0-9][A-Z0-9./_-]{3,63})"), "labeled transaction", "strong_identifier"),
    ("REFERENCE", re.compile(r"(?i)\b(?:reference|ref\s+(?:no|number|id)|case\s+reference)\b\s*[:#-]?\s*(?P<value>[A-Z0-9][A-Z0-9./_-]{3,63})"), "labeled reference", "strong_identifier"),
    ("INVOICE", _INVOICE, "labeled invoice", "strong_identifier"),
    ("PURCHASE_ORDER", re.compile(r"(?i)\b(?:purchase\s+order|po)\s*(?:number|no|#|id)?\s*[:#-]?\s*(?P<value>[A-Z0-9][A-Z0-9./_-]{2,63})"), "labeled purchase order", "strong_identifier"),
    ("VENDOR", re.compile(r"(?i)\bvendor\s+(?:id|code|number|no)\b\s*[:#-]?\s*(?P<value>[A-Z0-9][A-Z0-9./_-]{2,63})"), "labeled vendor identifier", "strong_identifier"),
    ("VESSEL", re.compile(r"(?i)\b(?:IMO|vessel\s+(?:id|number))\b\s*[:#-]?\s*(?P<value>\d{7})"), "labeled vessel identifier", "strong_identifier"),
    ("DEVICE", re.compile(r"(?i)\b(?:device\s+(?:id|identifier|serial)|IMEI|serial\s+number)\b\s*[:#-]?\s*(?P<value>[A-Z0-9][A-Z0-9./_-]{5,63})"), "labeled device identifier", "strong_identifier"),
    ("CASE_OR_DOCUMENT", re.compile(r"(?i)\b(?:case\s+(?:id|number|no)|document\s+(?:id|number|no))\b\s*[:#-]?\s*(?P<value>[A-Z0-9][A-Z0-9./_-]{3,63})"), "labeled case/document reference", "strong_identifier"),
)
_LABELED_NAME_OR_ORG = (
    ("ORGANIZATION", re.compile(r"(?i)\b(?:company|employer|supplier|vendor|bill\s+to)\s*[:\-]\s*(?P<value>[^|\n;]+)"), "labeled organization"),
    ("PERSON", re.compile(r"(?i)(?<!company )\b(?:full\s+name|employee\s+name|employee|name)\s*[:\-]\s*(?P<value>[^|\n;]+)"), "labeled person name"),
)


def _database_uuid(value, field_name: str) -> uuid.UUID:
    """Normalize UUID-like API/service IDs for SQLAlchemy UUID bind parameters."""
    if isinstance(value, uuid.UUID):
        return value
    try:
        return uuid.UUID(str(value))
    except (AttributeError, TypeError, ValueError) as exc:
        raise ValueError(f"{field_name} must be a valid UUID") from exc


def _normalize(kind: str, raw: str) -> str | None:
    value = unicodedata.normalize("NFKC", raw).strip().rstrip(".,;:")
    if kind == "EMAIL":
        return value.casefold() if len(value) <= 254 else None
    if kind == "PHONE":
        digits = re.sub(r"\D", "", value)
        return digits if 7 <= len(digits) <= 15 else None
    if kind == "IP":
        try:
            return ipaddress.ip_address(value).compressed.casefold()
        except ValueError:
            return None
    if kind == "INVOICE":
        # OCR/native table extraction can insert whitespace before a final
        # letter suffix. Only collapse that specifically supported shape;
        # preserve other spacing as evidence and do not alter other types.
        normalized = re.sub(r"-[ \t]+([A-Z])$", r"-\1", value, flags=re.IGNORECASE).upper()
        return normalized if 4 <= len(normalized) <= 64 and re.search(r"[A-Z0-9]", normalized) else None
    if kind == "BANK_ACCOUNT":
        normalized = re.sub(r"\s+", "", value).upper()
        return normalized if 6 <= len(re.sub(r"\W", "", normalized)) <= 34 else None
    normalized = re.sub(r"\s+", "", value).upper()
    return normalized if 4 <= len(normalized) <= 64 and re.search(r"[A-Z0-9]", normalized) else None


def extract_mentions(*, investigation_id, document_id, filename: str, source_sha256: str, blocks: list[dict]) -> list[dict]:
    """Extract typed occurrences from this document only; offsets preserve repeats."""
    mentions: list[dict] = []
    for block in blocks:
        if not isinstance(block, dict):
            continue
        block_id = str(block.get("block_id") or "")
        text = str(block.get("text") or "")
        if not block_id or not text:
            continue
        matches = [("EMAIL", match, "email address", "strong_identifier") for match in _EMAIL.finditer(text)]
        for evidence_type, pattern, label, strength in _RULES:
            matches.extend((evidence_type, match, label, strength) for match in pattern.finditer(text))
        for evidence_type, match, label, strength in matches:
            raw = match.group("value") if "value" in match.groupdict() and match.group("value") else match.group(0)
            normalized = _normalize(evidence_type, raw)
            if not normalized:
                continue
            start, end = match.span("value") if "value" in match.groupdict() and match.group("value") else match.span()
            page_index = block.get("page_index")
            mentions.append({
                "investigation_id": str(investigation_id), "document_id": str(document_id),
                "filename": filename, "source_sha256": source_sha256,
                "evidence_type": evidence_type, "original_text": raw,
                "normalized_value": normalized, "block_id": block_id,
                "page_number": page_index + 1 if isinstance(page_index, int) and not isinstance(page_index, bool) else None,
                "extraction_method": str(block.get("extraction_method") or "ocr"),
                "label_context": label, "start_offset": start, "end_offset": end,
                "coordinates": block.get("engine_bbox"), "strength": strength,
                "matcher_version": MATCHER_VERSION,
            })
    # These labels are extracted from the same OCR/native block model as the
    # exact identifiers. A delimiter-bounded value keeps adjacent labels apart
    # and preserves repeated labeled occurrences with their own offsets.
    for block in blocks:
        if not isinstance(block, dict):
            continue
        text = str(block.get("text") or "")
        if not block.get("block_id") or not text:
            continue
        for evidence_type, pattern, label in _LABELED_NAME_OR_ORG:
            for match in pattern.finditer(text):
                raw = match.group("value").strip().strip(" .,:;()[]{}")
                tokens = re.findall(r"[^\W_]+", raw, flags=re.UNICODE)
                if len(tokens) < 2 or len(raw) < 4:
                    continue
                if evidence_type == "PERSON" and any(len(token) < 2 for token in tokens):
                    continue
                start, end = match.span("value")
                while start < end and text[start].isspace():
                    start += 1
                while end > start and text[end - 1] in " .,:;()[]{}":
                    end -= 1
                raw = text[start:end]
                normalized = " ".join(unicodedata.normalize("NFKC", raw).casefold().split())
                mentions.append({
                    "investigation_id": str(investigation_id), "document_id": str(document_id),
                    "filename": filename, "source_sha256": source_sha256,
                    "evidence_type": evidence_type, "original_text": raw,
                    "normalized_value": normalized, "block_id": str(block.get("block_id") or ""),
                    "page_number": (block.get("page_index") + 1 if isinstance(block.get("page_index"), int) else None),
                    "extraction_method": str(block.get("extraction_method") or "ocr"),
                    "label_context": label, "start_offset": start, "end_offset": end,
                    "coordinates": block.get("engine_bbox"), "strength": "review_candidate",
                    "matcher_version": MATCHER_VERSION,
                })
    return mentions


def _candidate_key(investigation_id, evidence_type: str, normalized_value: str) -> str:
    raw = f"{investigation_id}|{evidence_type}|{normalized_value}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def extract_document_comparison_facts(*, document_id, filename: str, source_sha256: str, blocks: list[dict]) -> dict:
    """Persist compact, cited comparison facts for this document only.

    This avoids rebuilding comparisons by rereading or rescanning investigation
    OCR blocks at correlation-request time.
    """
    from app.services.ocr_metrics import extract_document_facts

    facts = extract_document_facts({
        "document_id": str(document_id), "filename": filename,
        "sha256": source_sha256, "blocks": blocks,
    })
    return {
        "document_id": str(document_id), "filename": filename,
        "source_sha256": source_sha256,
        "identifiers": [{
            "value": item["value"], "entity_type": item["entity_type"],
            "block_id": item.get("block_id"), "page_number": (
                item.get("page_index") + 1 if isinstance(item.get("page_index"), int) else None
            ), "quoted_text": item["value"],
        } for item in facts.get("identifiers", [])],
        "labeled_amounts": {
            label: {
                "raw": item.get("raw"), "value": item.get("value"),
                "normalization_warnings": item.get("normalization_warnings", []),
                "block_id": item.get("block_id"), "page_number": (
                    item.get("page_index") + 1 if isinstance(item.get("page_index"), int) else None
                ), "quoted_text": item.get("raw"),
            } for label, item in facts.get("labeled_amounts", {}).items()
        },
        "dates": [{
            "raw": item.get("raw"), "interpreted": item.get("interpreted"),
            "ambiguous": item.get("ambiguous", False), "block_id": item.get("block_id"),
            "page_number": item.get("page_index") + 1 if isinstance(item.get("page_index"), int) else None,
            "quoted_text": item.get("raw"),
        } for item in facts.get("dates", [])],
    }


def build_comparison_views(links: list[dict], facts_by_document: dict[str, dict]) -> tuple[list[dict], dict[str, dict]]:
    """Rebuild the legacy comparison shape from persisted compact facts only."""
    comparisons = []
    conflicting = {}
    comparable_types = {"INVOICE", "PURCHASE_ORDER", "TRANSACTION", "REFERENCE", "ACCOUNT", "VENDOR", "CASE_LINK", "DOCUMENT_ID"}
    for link in links:
        if link.get("candidate_key") is None or link.get("identifier_type") not in comparable_types or link.get("document_count", 0) < 2:
            continue
        entries = []
        distinct_totals = set()
        for document_id in link.get("document_ids", []):
            fact = facts_by_document.get(str(document_id))
            if fact is None:
                continue
            mentions = [item for item in fact.get("identifiers", []) if (
                item.get("entity_type") == link["identifier_type"]
                and item.get("value") == link["normalized_identifier"]
            )]
            if not mentions:
                continue
            amount = fact.get("labeled_amounts", {}).get("total")
            if amount and amount.get("value") is not None:
                try:
                    distinct_totals.add(round(float(amount["value"]), 2))
                except (TypeError, ValueError):
                    pass
            dates = [{
                "raw": date.get("raw"), "interpreted": date.get("interpreted"),
                "ambiguous": date.get("ambiguous", False),
                "evidence_ref": {
                    "document_id": str(document_id), "filename": fact.get("filename"),
                    "source_sha256": fact.get("source_sha256"), "page_number": date.get("page_number"),
                    "block_id": date.get("block_id"), "field_type": "date", "quoted_text": date.get("quoted_text"),
                },
            } for date in fact.get("dates", [])]
            total_ref = None
            if amount:
                total_ref = {
                    "document_id": str(document_id), "filename": fact.get("filename"),
                    "source_sha256": fact.get("source_sha256"), "page_number": amount.get("page_number"),
                    "block_id": amount.get("block_id"), "field_type": "total", "quoted_text": amount.get("quoted_text"),
                }
            entries.append({
                "document_id": str(document_id), "filename": fact.get("filename"),
                "total": ({"raw": amount.get("raw"), "value": amount.get("value"), "evidence_ref": total_ref} if amount else None),
                "dates": dates,
            })
        if len(entries) < 2:
            continue
        has_conflict = len(distinct_totals) > 1
        comparison = {
            "id": link["id"], "field": "document_identifier",
            "value": link["normalized_identifier"],
            "status": "value_conflict" if has_conflict else "matched_identifier",
            "documents": entries,
            "explanation": "The exact identifier is shared. Labeled totals differ across sources." if has_conflict else "The exact identifier is shared. Missing values are not treated as agreement.",
        }
        comparisons.append(comparison)
        if has_conflict:
            conflicting[link["normalized_identifier"]] = comparison
    return comparisons, conflicting


async def persist_document_mentions_and_candidates(db: AsyncSession, *, investigation_id, document_id, filename: str, source_sha256: str, blocks: list[dict]) -> dict:
    """Persist occurrences and refresh only candidate keys seen in this document."""
    database_investigation_id = _database_uuid(investigation_id, "investigation_id")
    database_document_id = _database_uuid(document_id, "document_id")
    # Serialize refreshes within one investigation on databases that support row
    # locks. This avoids two concurrent document commits publishing partial refs.
    await db.scalar(select(Investigation.id).where(Investigation.id == database_investigation_id).with_for_update())
    if await db.scalar(select(Document.id).where(
        Document.id == database_document_id, Document.investigation_id == database_investigation_id,
    )) is None:
        raise ValueError("Document does not belong to the requested investigation")
    mentions = extract_mentions(
        investigation_id=investigation_id, document_id=document_id,
        filename=filename, source_sha256=source_sha256, blocks=blocks,
    )
    keys = {(m["evidence_type"], m["normalized_value"]) for m in mentions}
    old_rows = (await db.scalars(select(DeterministicEvidenceMention).where(
        DeterministicEvidenceMention.document_id == database_document_id,
        DeterministicEvidenceMention.is_current.is_(True),
    ))).all()
    affected = keys | {(row.evidence_type, row.normalized_value) for row in old_rows}
    # Treat the incoming OCR/native blocks as the current snapshot for this
    # source. Existing exact occurrences are reactivated by the upsert below;
    # occurrences omitted by a changed extractor/OCR result become historical.
    await db.execute(update(DeterministicEvidenceMention).where(
        DeterministicEvidenceMention.document_id == database_document_id,
        DeterministicEvidenceMention.is_current.is_(True),
    ).values(is_current=False))
    dialect = db.bind.dialect.name if db.bind is not None else ""
    table = DeterministicEvidenceMention.__table__
    if mentions:
        database_mentions = [{
            **item,
            "investigation_id": database_investigation_id,
            "document_id": database_document_id,
        } for item in mentions]
        insert = sqlite_insert(table) if dialect == "sqlite" else pg_insert(table) if dialect == "postgresql" else None
        if insert is None:
            for item in database_mentions:
                existing = await db.scalar(select(DeterministicEvidenceMention).where(
                    DeterministicEvidenceMention.document_id == database_document_id,
                    DeterministicEvidenceMention.source_sha256 == source_sha256,
                    DeterministicEvidenceMention.block_id == item["block_id"],
                    DeterministicEvidenceMention.evidence_type == item["evidence_type"],
                    DeterministicEvidenceMention.normalized_value == item["normalized_value"],
                    DeterministicEvidenceMention.start_offset == item["start_offset"],
                    DeterministicEvidenceMention.end_offset == item["end_offset"],
                    DeterministicEvidenceMention.matcher_version == item["matcher_version"],
                ))
                if existing:
                    existing.is_current = True
                else:
                    db.add(DeterministicEvidenceMention(**item))
        else:
            insert = insert.values(database_mentions)
            insert = insert.on_conflict_do_update(index_elements=[
                "document_id", "source_sha256", "block_id", "evidence_type",
                "normalized_value", "start_offset", "end_offset", "matcher_version",
            ], set_={"is_current": True})
            await db.execute(insert)

    updated = []
    for evidence_type, normalized in affected:
        refs = (await db.scalars(select(DeterministicEvidenceMention).where(
            DeterministicEvidenceMention.investigation_id == database_investigation_id,
            DeterministicEvidenceMention.evidence_type == evidence_type,
            DeterministicEvidenceMention.normalized_value == normalized,
            DeterministicEvidenceMention.is_current.is_(True),
        ).order_by(DeterministicEvidenceMention.document_id, DeterministicEvidenceMention.block_id, DeterministicEvidenceMention.start_offset))).all()
        document_ids = sorted({str(row.document_id) for row in refs})
        candidate_key = _candidate_key(investigation_id, evidence_type, normalized)
        candidate = await db.scalar(select(RelationshipCandidate).where(
            RelationshipCandidate.investigation_id == database_investigation_id,
            RelationshipCandidate.candidate_key == candidate_key,
        ))
        if len(document_ids) < 2 and candidate is None:
            # A candidate is a cross-document relationship. Keep lone mentions
            # in the occurrence table without exposing them as candidates.
            continue
        evidence_refs = [{
            "investigation_id": str(row.investigation_id), "document_id": str(row.document_id),
            "filename": row.filename, "source_sha256": row.source_sha256,
            "field_type": row.evidence_type, "raw_value": row.original_text,
            "normalized_value": row.normalized_value, "block_id": row.block_id,
            "page_number": row.page_number, "extraction_method": row.extraction_method,
            "label_context": row.label_context, "start_offset": row.start_offset,
            "end_offset": row.end_offset, "bounding_box": row.coordinates,
            "evidence_strength": row.strength,
        } for row in refs]
        lifecycle = "active" if len(document_ids) > 1 else "stale"
        now = datetime.now(timezone.utc)
        if candidate is None:
            legacy_review_nodes = (await db.scalars(select(DerivedGraphNode).where(
                DerivedGraphNode.investigation_id == database_investigation_id,
                DerivedGraphNode.entity_type == evidence_type,
                DerivedGraphNode.label == normalized,
            ))).all()
            legacy_review = resolve_legacy_candidate_review(
                investigation_id=database_investigation_id,
                evidence_type=evidence_type,
                normalized_value=normalized,
                candidate_document_ids=document_ids,
                legacy_nodes=legacy_review_nodes,
            )
            mapped_status = (
                legacy_review["review_status"]
                if legacy_review["status"] == "matched" else "pending"
            )
            reviewed_by = (
                consistent_legacy_review_field(legacy_review, "reviewed_by_user_id")
                if legacy_review["status"] == "matched" else None
            )
            candidate_values = dict(
                investigation_id=database_investigation_id, candidate_key=candidate_key,
                evidence_type=evidence_type, normalized_value=normalized,
                lifecycle_status=lifecycle,
                review_status=("pending" if mapped_status == "candidate" else mapped_status),
                reviewed_by_user_id=(uuid.UUID(reviewed_by) if reviewed_by else None),
                reviewed_at=(consistent_legacy_review_field(legacy_review, "reviewed_at") if legacy_review["status"] == "matched" else None),
                review_reason=(consistent_legacy_review_field(legacy_review, "review_reason") if legacy_review["status"] == "matched" else None),
                # Legacy nodes retain only their current review fields. Do not
                # fabricate a history event from that current state.
                review_history=[],
                evidence_refs=evidence_refs, document_ids=document_ids,
                explanation=f"Exact {evidence_type.lower().replace('_', ' ')} match across source documents; candidate only.",
                first_seen_at=now, last_seen_at=now,
            )
            if dialect == "sqlite":
                candidate_insert = sqlite_insert(RelationshipCandidate.__table__).values(**candidate_values)
                candidate_insert = candidate_insert.on_conflict_do_update(
                    index_elements=["investigation_id", "candidate_key"],
                    set_={key: candidate_values[key] for key in (
                        "lifecycle_status", "evidence_refs", "document_ids", "explanation", "last_seen_at",
                    )},
                )
                await db.execute(candidate_insert)
            elif dialect == "postgresql":
                candidate_insert = pg_insert(RelationshipCandidate.__table__).values(**candidate_values)
                candidate_insert = candidate_insert.on_conflict_do_update(
                    index_elements=["investigation_id", "candidate_key"],
                    set_={key: candidate_values[key] for key in (
                        "lifecycle_status", "evidence_refs", "document_ids", "explanation", "last_seen_at",
                    )},
                )
                await db.execute(candidate_insert)
            else:
                db.add(RelationshipCandidate(**candidate_values))
            candidate = await db.scalar(select(RelationshipCandidate).where(
                RelationshipCandidate.investigation_id == database_investigation_id,
                RelationshipCandidate.candidate_key == candidate_key,
            ))
        else:
            candidate.lifecycle_status = lifecycle
            candidate.evidence_refs = evidence_refs
            candidate.document_ids = document_ids
            if lifecycle == "active":
                candidate.last_seen_at = now
            candidate.explanation = f"Exact {evidence_type.lower().replace('_', ' ')} match across source documents; candidate only."
        updated.append(candidate)
    return {"mentions": mentions, "candidates": updated, "mention_count": len(mentions), "candidate_count": len(updated)}

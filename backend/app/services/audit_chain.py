import hashlib
import json
import uuid
from datetime import datetime, timezone
from typing import Any, Sequence

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.entities import AuditChainEvent


def event_hash(previous_hash: str, payload: dict) -> str:
    body = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(f"{previous_hash}\n{body}".encode()).hexdigest()


def _event_payload(event: AuditChainEvent) -> dict[str, Any]:
    created_at = event.created_at
    if created_at.tzinfo is None:
        created_at = created_at.replace(tzinfo=timezone.utc)
    else:
        created_at = created_at.astimezone(timezone.utc)
    return {
        "id": str(event.id),
        "investigation_id": str(event.investigation_id),
        "actor_user_id": str(event.actor_user_id) if event.actor_user_id else None,
        "action": event.action,
        "target_type": event.target_type,
        "target_id": event.target_id,
        "details": event.details or {},
        "created_at": created_at.isoformat(),
    }


def verify_audit_chain(events: Sequence[AuditChainEvent]) -> dict[str, Any]:
    next_by_previous: dict[str, AuditChainEvent] = {}
    for event in events:
        if event.previous_hash in next_by_previous:
            return {
                "valid": False,
                "event_count": len(events),
                "verified_count": 0,
                "broken_event_id": str(event.id),
                "reason": "The audit chain contains a fork.",
            }
        next_by_previous[event.previous_hash] = event

    current_hash = "genesis"
    verified: set[str] = set()
    while current_hash in next_by_previous:
        event = next_by_previous[current_hash]
        if event_hash(event.previous_hash, _event_payload(event)) != event.event_hash:
            return {
                "valid": False,
                "event_count": len(events),
                "verified_count": len(verified),
                "broken_event_id": str(event.id),
                "reason": "An audit event does not match its stored hash.",
            }
        if event.event_hash in verified:
            return {
                "valid": False,
                "event_count": len(events),
                "verified_count": len(verified),
                "broken_event_id": str(event.id),
                "reason": "The audit chain contains a cycle.",
            }
        verified.add(event.event_hash)
        current_hash = event.event_hash

    if len(verified) != len(events):
        broken = next(event for event in events if event.event_hash not in verified)
        return {
            "valid": False,
            "event_count": len(events),
            "verified_count": len(verified),
            "broken_event_id": str(broken.id),
            "reason": "The audit chain has a missing link or an unexpected root.",
        }

    return {
        "valid": True,
        "event_count": len(events),
        "verified_count": len(verified),
        "broken_event_id": None,
        "reason": None,
    }


async def append_audit(
    db: AsyncSession,
    *,
    investigation_id,
    actor_user_id,
    action: str,
    target_type: str,
    target_id: str,
    details: dict | None = None,
) -> AuditChainEvent:
    previous = await db.scalar(
        select(AuditChainEvent)
        .where(AuditChainEvent.investigation_id == investigation_id)
        .order_by(AuditChainEvent.created_at.desc())
    )
    previous_hash = previous.event_hash if previous else "genesis"
    event_id = uuid.uuid4()
    created_at = datetime.now(timezone.utc)
    record = AuditChainEvent(
        id=event_id,
        investigation_id=investigation_id,
        actor_user_id=actor_user_id,
        action=action,
        target_type=target_type,
        target_id=target_id,
        details=details or {},
        previous_hash=previous_hash,
        event_hash=event_hash(previous_hash, {
            "id": str(event_id),
            "investigation_id": str(investigation_id),
            "actor_user_id": str(actor_user_id) if actor_user_id else None,
            "action": action,
            "target_type": target_type,
            "target_id": target_id,
            "details": details or {},
            "created_at": created_at.isoformat(),
        }),
        created_at=created_at,
    )
    db.add(record)
    return record

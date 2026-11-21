"""Conservative projection of legacy graph review state onto indexed candidates."""
from __future__ import annotations

from datetime import datetime
from typing import Iterable


_PENDING_STATUSES = {"candidate", "pending"}


def _as_text(value: object) -> str:
    return str(value) if value is not None else ""


def _document_ids(values: object) -> set[str]:
    if not isinstance(values, (list, tuple, set)):
        return set()
    return {str(value) for value in values if value is not None and str(value)}


def _iso(value: object) -> str | None:
    return value.isoformat() if isinstance(value, datetime) else None


def resolve_legacy_candidate_review(
    *,
    investigation_id: object,
    evidence_type: str,
    normalized_value: str,
    candidate_document_ids: Iterable[str],
    legacy_nodes: Iterable[object],
) -> dict:
    """Resolve matching legacy reviews without fuzzy matching or row-order choice.

    Identity matching is exact on investigation, type, and label/value. When both
    sides contain source document IDs, they must overlap. Any exact-identity row
    whose source compatibility cannot be established blocks inheritance rather
    than being silently ignored.
    """
    investigation = _as_text(investigation_id)
    target_documents = {str(value) for value in candidate_document_ids if value is not None and str(value)}
    exact_identity = [
        node for node in legacy_nodes
        if _as_text(getattr(node, "investigation_id", "")) == investigation
        and _as_text(getattr(node, "entity_type", "")) == evidence_type
        and _as_text(getattr(node, "label", "")) == normalized_value
    ]
    if not exact_identity:
        return {
            "status": "unmatched",
            "reason": "no_exact_legacy_identity",
            "review_status": None,
            "legacy_node_ids": [],
            "decisions": [],
            "review_metadata": [],
            "review_history_available": False,
            "review_history_limitation": None,
        }

    source_incompatible = []
    for node in exact_identity:
        node_documents = _document_ids(getattr(node, "document_ids", None))
        if not target_documents or not node_documents or not (target_documents & node_documents):
            source_incompatible.append(node)

    ordered_nodes = sorted(exact_identity, key=lambda item: _as_text(getattr(item, "canonical_id", "")))
    metadata = [{
        "legacy_node_id": _as_text(getattr(node, "canonical_id", "")),
        "review_status": _as_text(getattr(node, "review_status", "candidate")),
        "reviewed_by_user_id": (
            str(getattr(node, "reviewed_by_user_id", None))
            if getattr(node, "reviewed_by_user_id", None) is not None else None
        ),
        "reviewed_at": _iso(getattr(node, "reviewed_at", None)),
        "review_reason": getattr(node, "review_reason", None),
    } for node in ordered_nodes]
    node_ids = [item["legacy_node_id"] for item in metadata]

    reviewed_decisions = {
        item["review_status"] for item in metadata
        if item["review_status"] not in _PENDING_STATUSES
    }
    if source_incompatible and reviewed_decisions:
        return {
            "status": "ambiguous",
            "reason": "source_evidence_compatibility_not_established",
            "review_status": None,
            "legacy_node_ids": node_ids,
            "decisions": sorted({item["review_status"] for item in metadata}),
            "review_metadata": metadata,
            "review_history_available": False,
            "review_history_limitation": "DerivedGraphNode stores only the current review fields, not full review history.",
        }
    if source_incompatible:
        return {
            "status": "unmatched",
            "reason": "pending_review_has_no_verified_source_overlap",
            "review_status": None,
            "legacy_node_ids": node_ids,
            "decisions": ["candidate"],
            "review_metadata": metadata,
            "review_history_available": False,
            "review_history_limitation": "DerivedGraphNode stores only the current review fields, not full review history.",
        }

    if len(reviewed_decisions) > 1:
        return {
            "status": "ambiguous",
            "reason": "conflicting_legacy_review_decisions",
            "review_status": None,
            "legacy_node_ids": node_ids,
            "decisions": sorted(reviewed_decisions),
            "review_metadata": metadata,
            "review_history_available": False,
            "review_history_limitation": "DerivedGraphNode stores only the current review fields, not full review history.",
        }

    decision = next(iter(reviewed_decisions), "candidate")
    return {
        "status": "matched",
        "reason": "exact_identity_and_source_documents_match",
        "review_status": decision,
        "legacy_node_ids": node_ids,
        "decisions": sorted(reviewed_decisions) if reviewed_decisions else ["candidate"],
        "review_metadata": metadata,
        "review_history_available": False,
        "review_history_limitation": "DerivedGraphNode stores only the current review fields, not full review history.",
    }


def consistent_legacy_review_field(resolution: dict, field: str):
    """Return a legacy scalar field only when every mapped record agrees."""
    metadata = resolution.get("review_metadata") or []
    values = {item.get(field) for item in metadata}
    return next(iter(values)) if len(values) == 1 else None


def project_candidate_review(indexed_status: str | None, legacy_resolution: dict) -> dict:
    """Project review state with indexed decisions taking precedence safely."""
    if indexed_status not in {None, "pending"}:
        return {"review_status": indexed_status, "review_source": "indexed_candidate"}
    if legacy_resolution.get("status") == "matched":
        return {
            "review_status": legacy_resolution["review_status"],
            "review_source": "legacy_projection",
        }
    if legacy_resolution.get("status") == "ambiguous":
        return {"review_status": "ambiguous", "review_source": "ambiguous_legacy_projection"}
    return {"review_status": "candidate", "review_source": None}


def review_history_status(indexed_history: object, legacy_resolution: dict) -> str:
    """Describe the gap without representing current legacy state as history."""
    has_indexed_history = bool(indexed_history)
    if legacy_resolution.get("status") in {"matched", "ambiguous"}:
        return "indexed_history_with_legacy_gap" if has_indexed_history else "legacy_history_unavailable"
    return "recorded" if has_indexed_history else "not_recorded"

from datetime import datetime, timezone
from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.services.relationship_review import project_candidate_review, resolve_legacy_candidate_review, review_history_status


INVESTIGATION_ID = uuid4()
DOCUMENT_IDS = [str(uuid4()), str(uuid4())]


def legacy_node(
    canonical_id,
    *,
    investigation_id=INVESTIGATION_ID,
    entity_type="INVOICE",
    label="INV-10001",
    review_status="candidate",
    documents=None,
    reviewed_by_user_id=None,
    reviewed_at=None,
    review_reason=None,
):
    return SimpleNamespace(
        canonical_id=canonical_id,
        investigation_id=investigation_id,
        entity_type=entity_type,
        label=label,
        document_ids=list(DOCUMENT_IDS if documents is None else documents),
        review_status=review_status,
        reviewed_by_user_id=reviewed_by_user_id,
        reviewed_at=reviewed_at,
        review_reason=review_reason,
    )


def resolve(*nodes, investigation_id=INVESTIGATION_ID, evidence_type="INVOICE", value="INV-10001", documents=None):
    return resolve_legacy_candidate_review(
        investigation_id=investigation_id,
        evidence_type=evidence_type,
        normalized_value=value,
        candidate_document_ids=DOCUMENT_IDS if documents is None else documents,
        legacy_nodes=nodes,
    )


def test_one_pending_legacy_node_stays_unreviewed_and_reports_history_limit():
    result = resolve(legacy_node("det:identifier:invoice:INV-10001"))
    assert result["status"] == "matched"
    assert result["review_status"] == "candidate"
    assert result["review_history_available"] is False
    assert "not full review history" in result["review_history_limitation"]


@pytest.mark.parametrize("status", ["confirmed", "rejected"])
def test_one_reviewed_legacy_node_maps_only_its_current_decision(status):
    reviewed_at = datetime(2026, 1, 2, tzinfo=timezone.utc)
    result = resolve(legacy_node(
        "legacy-node", review_status=status, reviewed_at=reviewed_at,
        reviewed_by_user_id="reviewer-1", review_reason="Synthetic reason",
    ))
    assert result["status"] == "matched"
    assert result["review_status"] == status
    assert result["review_metadata"][0] == {
        "legacy_node_id": "legacy-node", "review_status": status,
        "reviewed_by_user_id": "reviewer-1", "reviewed_at": reviewed_at.isoformat(),
        "review_reason": "Synthetic reason",
    }


def test_multiple_pending_nodes_retain_pending_without_arbitrary_metadata():
    result = resolve(
        legacy_node("legacy-b"), legacy_node("legacy-a", review_reason="not a review decision"),
    )
    assert result["status"] == "matched"
    assert result["review_status"] == "candidate"
    assert result["legacy_node_ids"] == ["legacy-a", "legacy-b"]
    assert result["decisions"] == ["candidate"]


def test_multiple_agreeing_confirmed_nodes_map_decision_and_keep_all_metadata():
    first_at = datetime(2026, 1, 2, tzinfo=timezone.utc)
    second_at = datetime(2026, 2, 2, tzinfo=timezone.utc)
    result = resolve(
        legacy_node("legacy-b", review_status="confirmed", reviewed_at=second_at, review_reason="second"),
        legacy_node("legacy-a", review_status="confirmed", reviewed_at=first_at, review_reason="first"),
    )
    assert result["status"] == "matched"
    assert result["review_status"] == "confirmed"
    assert [item["review_reason"] for item in result["review_metadata"]] == ["first", "second"]


@pytest.mark.parametrize("reverse", [False, True])
def test_conflicting_decisions_are_ambiguous_independent_of_input_order(reverse):
    nodes = [
        legacy_node("confirmed", review_status="confirmed"),
        legacy_node("rejected", review_status="rejected"),
    ]
    if reverse:
        nodes.reverse()
    result = resolve(*nodes)
    assert result["status"] == "ambiguous"
    assert result["review_status"] is None
    assert result["decisions"] == ["confirmed", "rejected"]
    assert result["legacy_node_ids"] == ["confirmed", "rejected"]
    assert project_candidate_review("pending", result) == {
        "review_status": "ambiguous", "review_source": "ambiguous_legacy_projection",
    }


def test_conflicting_review_timestamps_do_not_break_tie_or_pick_a_winner():
    result = resolve(
        legacy_node("later-confirmed", review_status="confirmed", reviewed_at=datetime(2026, 5, 1, tzinfo=timezone.utc)),
        legacy_node("earlier-rejected", review_status="rejected", reviewed_at=datetime(2026, 1, 1, tzinfo=timezone.utc)),
    )
    assert result["status"] == "ambiguous"
    assert result["review_status"] is None


def test_same_label_in_separate_investigations_does_not_cross_map():
    result = resolve(
        legacy_node("other-investigation", investigation_id=uuid4(), review_status="confirmed"),
    )
    assert result["status"] == "unmatched"
    assert result["reason"] == "no_exact_legacy_identity"


def test_same_label_with_different_evidence_type_does_not_map():
    result = resolve(legacy_node("other-type", entity_type="TRANSACTION", review_status="confirmed"))
    assert result["status"] == "unmatched"


def test_similar_but_differently_normalized_labels_do_not_fuzzy_match():
    result = resolve(legacy_node("different-label", label="inv 10001", review_status="confirmed"))
    assert result["status"] == "unmatched"


def test_exact_identity_without_source_overlap_cannot_inherit_review():
    result = resolve(legacy_node("wrong-source", review_status="confirmed", documents=[str(uuid4())]))
    assert result["status"] == "ambiguous"
    assert result["reason"] == "source_evidence_compatibility_not_established"


def test_indexed_candidate_decision_wins_without_being_replaced_by_legacy_ambiguity():
    conflict = resolve(
        legacy_node("legacy-confirmed", review_status="confirmed"),
        legacy_node("legacy-rejected", review_status="rejected"),
    )
    assert project_candidate_review("confirmed", conflict) == {
        "review_status": "confirmed", "review_source": "indexed_candidate",
    }
    assert project_candidate_review("rejected", conflict)["review_status"] == "rejected"


def test_missing_legacy_history_is_never_manufactured():
    result = resolve(legacy_node("no-history", review_status="confirmed"))
    assert result["review_history_available"] is False
    assert result["review_metadata"][0]["review_status"] == "confirmed"
    assert "history" not in result["review_metadata"][0]
    assert review_history_status([], result) == "legacy_history_unavailable"
    assert review_history_status([{"status": "confirmed"}], result) == "indexed_history_with_legacy_gap"


def test_unverified_source_overlap_with_pending_nodes_does_not_invent_a_decision():
    result = resolve(legacy_node("pending-no-source", documents=[]), documents=DOCUMENT_IDS)
    assert result["status"] == "unmatched"
    assert result["decisions"] == ["candidate"]
    assert project_candidate_review("pending", result)["review_status"] == "candidate"

import asyncio
import uuid
from datetime import datetime, timezone

import pytest
from sqlalchemy import func, select

from app.models.entities import (
    DerivedGraphNode, DeterministicEvidenceMention, Document, Investigation, RelationshipCandidate, User,
)
from app.services.relationship_index import (
    _candidate_key, _database_uuid, build_comparison_views, extract_document_comparison_facts,
    extract_mentions, persist_document_mentions_and_candidates,
)


def test_mentions_preserve_repeated_source_occurrences_and_provenance():
    investigation_id, document_id = uuid.uuid4(), uuid.uuid4()
    block = {"block_id": "p1-b4", "page_index": 0, "extraction_method": "native", "text": "Email: Same@Example.test; second email same@example.test"}
    mentions = extract_mentions(
        investigation_id=investigation_id, document_id=document_id,
        filename="synthetic.pdf", source_sha256="a" * 64, blocks=[block],
    )
    emails = [item for item in mentions if item["evidence_type"] == "EMAIL"]
    assert len(emails) == 2
    assert emails[0]["normalized_value"] == emails[1]["normalized_value"] == "same@example.test"
    assert emails[0]["start_offset"] != emails[1]["start_offset"]
    assert all(item["block_id"] == "p1-b4" and item["page_number"] == 1 for item in emails)


def test_labeled_identifiers_and_repeated_person_fields_keep_correct_values_and_offsets():
    text = "Transaction ID: TX-43991; Full Name: Ana Maria Silva; Full Name: Ana Maria Silva"
    mentions = extract_mentions(
        investigation_id=uuid.uuid4(), document_id=uuid.uuid4(),
        filename="synthetic.pdf", source_sha256="b" * 64,
        blocks=[{"block_id": "native-2", "page_index": 1, "extraction_method": "native", "text": text}],
    )
    transaction = next(item for item in mentions if item["evidence_type"] == "TRANSACTION")
    people = [item for item in mentions if item["evidence_type"] == "PERSON"]
    assert transaction["normalized_value"] == "TX-43991"
    assert len(people) == 2 and people[0]["start_offset"] != people[1]["start_offset"]


@pytest.mark.parametrize(
    ("text", "expected_raw", "expected_normalized"),
    [
        ("INV-2026-0894-B", "2026-0894-B", "2026-0894-B"),
        ("INV-2026-0894- B", "2026-0894- B", "2026-0894-B"),
        ("Invoice No: 2026-0894-B", "2026-0894-B", "2026-0894-B"),
        ("INV-2026-0894-", "2026-0894-", "2026-0894-"),
        ("INV-2026-0894-C", "2026-0894-C", "2026-0894-C"),
    ],
)
def test_invoice_final_letter_suffix_normalization_is_narrow_and_preserves_source(text, expected_raw, expected_normalized):
    block = {
        "block_id": "invoice-source-block", "page_index": 1,
        "extraction_method": "native_pdf_text", "text": text,
    }
    mentions = extract_mentions(
        investigation_id=uuid.uuid4(), document_id=uuid.uuid4(),
        filename="synthetic-invoice.pdf", source_sha256="c" * 64,
        blocks=[block],
    )
    invoice = next(item for item in mentions if item["evidence_type"] == "INVOICE")

    assert invoice["original_text"] == expected_raw
    assert invoice["normalized_value"] == expected_normalized
    assert block["text"][invoice["start_offset"]:invoice["end_offset"]] == expected_raw
    assert invoice["block_id"] == block["block_id"]
    assert invoice["source_sha256"] == "c" * 64
    assert invoice["page_number"] == 2


def test_invoice_internal_space_is_not_joined_and_repeated_suffixes_keep_distinct_offsets():
    investigation_id, document_id = uuid.uuid4(), uuid.uuid4()
    text = "INV-20 26-0894-B | INV-2026-0894- B; INV-2026-0894- B"
    mentions = extract_mentions(
        investigation_id=investigation_id, document_id=document_id,
        filename="synthetic-invoice.pdf", source_sha256="d" * 64,
        blocks=[{"block_id": "repeat-block", "page_index": 0, "text": text}],
    )
    invoices = [item for item in mentions if item["evidence_type"] == "INVOICE"]
    repaired = [item for item in invoices if item["normalized_value"] == "2026-0894-B"]

    assert len(invoices) == 2
    assert len(repaired) == 2
    assert repaired[0]["start_offset"] != repaired[1]["start_offset"]
    assert all(text[item["start_offset"]:item["end_offset"]] == item["original_text"] for item in repaired)
    assert all(item["block_id"] == "repeat-block" and item["source_sha256"] == "d" * 64 for item in repaired)


def test_normalized_invoice_candidate_identity_remains_investigation_scoped():
    first_investigation, second_investigation = uuid.uuid4(), uuid.uuid4()
    first_mentions = extract_mentions(
        investigation_id=first_investigation, document_id=uuid.uuid4(),
        filename="a.pdf", source_sha256="e" * 64,
        blocks=[{"block_id": "a1", "text": "INV-2026-0894- B"}],
    )
    second_mentions = extract_mentions(
        investigation_id=second_investigation, document_id=uuid.uuid4(),
        filename="b.pdf", source_sha256="f" * 64,
        blocks=[{"block_id": "b1", "text": "INV-2026-0894-B"}],
    )
    first = next(item for item in first_mentions if item["evidence_type"] == "INVOICE")
    second = next(item for item in second_mentions if item["evidence_type"] == "INVOICE")

    assert first["normalized_value"] == second["normalized_value"] == "2026-0894-B"
    assert _candidate_key(first_investigation, "INVOICE", first["normalized_value"]) != _candidate_key(
        second_investigation, "INVOICE", second["normalized_value"],
    )


def test_candidate_identity_is_repeatable_and_investigation_scoped():
    first_investigation, second_investigation = uuid.uuid4(), uuid.uuid4()
    first = _candidate_key(first_investigation, "EMAIL", "same@example.test")
    assert first == _candidate_key(first_investigation, "EMAIL", "same@example.test")
    assert first != _candidate_key(second_investigation, "EMAIL", "same@example.test")


def test_database_uuid_normalization_accepts_strings_and_uuid_objects():
    value = uuid.uuid4()
    assert _database_uuid(str(value), "document_id") == value
    assert _database_uuid(value, "document_id") is value
    with pytest.raises(ValueError, match="document_id must be a valid UUID"):
        _database_uuid("not-a-uuid", "document_id")


def test_schema_indexes_preserve_occurrence_and_candidate_uniqueness():
    mention_indexes = {index.name: index for index in DeterministicEvidenceMention.__table__.indexes}
    candidate_indexes = {index.name: index for index in RelationshipCandidate.__table__.indexes}
    occurrence = mention_indexes["uq_det_mentions_source_occurrence"]
    assert occurrence.unique is True
    assert [column.name for column in occurrence.columns][-3:] == ["start_offset", "end_offset", "matcher_version"]
    assert [column.name for column in mention_indexes["ix_det_mentions_investigation_key"].columns] == [
        "investigation_id", "evidence_type", "normalized_value",
    ]
    assert candidate_indexes["uq_relationship_candidates_key"].unique is True


def test_legacy_comparison_shape_uses_persisted_compact_facts_and_citations():
    fact_a = extract_document_comparison_facts(
        document_id="doc-a", filename="a.pdf", source_sha256="a" * 64,
        blocks=[{"block_id": "a1", "page_index": 0, "text": "Invoice: INV-10001\nTotal: 100.00\nDate: 2026-01-02"}],
    )
    fact_b = extract_document_comparison_facts(
        document_id="doc-b", filename="b.pdf", source_sha256="b" * 64,
        blocks=[{"block_id": "b1", "page_index": 1, "text": "Invoice: INV-10001\nTotal: 125.00\nDate: 2026-01-03"}],
    )
    comparisons, conflicts = build_comparison_views([{
        "id": "detrel:stable", "candidate_key": "candidate-key",
        "identifier_type": "INVOICE", "normalized_identifier": "INV-10001",
        "document_ids": ["doc-a", "doc-b"], "document_count": 2,
    }], {"doc-a": fact_a, "doc-b": fact_b})
    assert len(comparisons) == 1
    assert comparisons[0]["status"] == "value_conflict"
    assert comparisons[0]["documents"][0]["total"]["evidence_ref"]["block_id"] == "a1"
    assert conflicts["INV-10001"]["id"] == "detrel:stable"


@pytest.mark.asyncio
async def test_index_is_idempotent_and_tracks_seventh_document_lifecycle(client):
    user_id, investigation_id = uuid.uuid4(), uuid.uuid4()
    # The client fixture owns only an in-memory SQLite database.
    if client is None:
        raise AssertionError("isolated client fixture missing")
    app = __import__("app.main", fromlist=["app"]).app
    async with app.state.test_sessions() as db:
        db.add(User(id=user_id, full_name="Synthetic Reviewer", email="index-test@example.test", password_hash="synthetic"))
        db.add(Investigation(id=investigation_id, case_number="SYN-REL-001", title="Synthetic relationship test", created_by_user_id=user_id))
        await db.flush()
        docs = []
        for index in range(7):
            doc = Document(
                id=uuid.uuid4(), investigation_id=investigation_id, filename=f"synthetic-{index}.pdf",
                content_type="application/pdf", size_bytes=1, sha256=f"{index + 1:064x}",
            )
            db.add(doc)
            docs.append(doc)
        await db.commit()

    async def process(doc, text, sha, *, string_ids=False):
        async with app.state.test_sessions() as session:
            result = await persist_document_mentions_and_candidates(
                session,
                investigation_id=str(investigation_id) if string_ids else investigation_id,
                document_id=str(doc.id) if string_ids else doc.id,
                filename=doc.filename, source_sha256=sha,
                blocks=[{"block_id": "synthetic-b1", "page_index": 0, "extraction_method": "ocr", "text": text}],
            )
            await session.commit()
            return result

    shared = "Email: repeated@example.test and again repeated@example.test"
    for index, doc in enumerate(docs):
        await process(doc, shared, doc.sha256, string_ids=(index == 0))

    # The final document was processed twice; occurrence identity is stable.
    await process(docs[-1], shared, docs[-1].sha256)
    async with app.state.test_sessions() as db:
        candidate = await db.scalar(select(RelationshipCandidate).where(RelationshipCandidate.investigation_id == investigation_id))
        assert candidate is not None
        assert candidate.investigation_id == investigation_id
        assert candidate.lifecycle_status == "active"
        assert len(candidate.document_ids) == 7
        stable_candidate_key = candidate.candidate_key
        candidate.review_status = "confirmed"
        candidate.review_reason = "Synthetic verification"
        candidate.review_history = [{"status": "confirmed", "reason": "Earlier synthetic review"}]
        await db.commit()

        await persist_document_mentions_and_candidates(
            db, investigation_id=investigation_id, document_id=docs[-1].id,
            filename=docs[-1].filename, source_sha256="f" * 64,
            blocks=[{"block_id": "synthetic-b1", "page_index": 0, "extraction_method": "ocr", "text": "No shared identifiers in this source"}],
        )
        await db.commit()
        candidate = await db.scalar(select(RelationshipCandidate).where(RelationshipCandidate.investigation_id == investigation_id))
        assert candidate.lifecycle_status == "active"  # six documents still support it
        assert candidate.review_status == "confirmed"

        await persist_document_mentions_and_candidates(
            db, investigation_id=investigation_id, document_id=docs[0].id,
            filename=docs[0].filename, source_sha256="e" * 64,
            blocks=[{"block_id": "synthetic-b1", "page_index": 0, "extraction_method": "ocr", "text": "No shared identifiers in this source"}],
        )
        for doc in docs[1:6]:
            await persist_document_mentions_and_candidates(
                db, investigation_id=investigation_id, document_id=doc.id,
                filename=doc.filename, source_sha256=f"{doc.sha256[:62]}ff",
                blocks=[{"block_id": "synthetic-b1", "page_index": 0, "extraction_method": "ocr", "text": "No shared identifiers in this source"}],
            )
        await db.commit()
        candidate = await db.scalar(select(RelationshipCandidate).where(RelationshipCandidate.investigation_id == investigation_id))
        assert candidate.lifecycle_status == "stale"
        assert candidate.review_status == "confirmed"
        assert candidate.review_history == [{"status": "confirmed", "reason": "Earlier synthetic review"}]
        assert candidate.candidate_key == stable_candidate_key
        count = await db.scalar(select(func.count(DeterministicEvidenceMention.id)).where(
            DeterministicEvidenceMention.investigation_id == investigation_id,
            DeterministicEvidenceMention.evidence_type == "EMAIL",
        ))
        assert count == 14
        persisted_investigation_ids = set((await db.scalars(
            select(DeterministicEvidenceMention.investigation_id).where(
                DeterministicEvidenceMention.document_id.in_([doc.id for doc in docs])
            )
        )).all())
        assert persisted_investigation_ids == {investigation_id}
        for doc in docs[:2]:
            await persist_document_mentions_and_candidates(
                db, investigation_id=investigation_id, document_id=doc.id,
                filename=doc.filename, source_sha256=doc.sha256,
                blocks=[{"block_id": "synthetic-b1", "page_index": 0, "extraction_method": "ocr", "text": shared}],
            )
        await db.commit()
        candidate = await db.scalar(select(RelationshipCandidate).where(RelationshipCandidate.investigation_id == investigation_id))
        assert candidate.lifecycle_status == "active"
        assert candidate.review_status == "confirmed"
        assert candidate.review_history == [{"status": "confirmed", "reason": "Earlier synthetic review"}]
        assert candidate.candidate_key == stable_candidate_key


@pytest.mark.asyncio
async def test_concurrent_duplicate_document_refreshes_are_idempotent(client):
    user_id, investigation_id, document_id = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    app = __import__("app.main", fromlist=["app"]).app
    async with app.state.test_sessions() as db:
        db.add(User(id=user_id, full_name="Synthetic Reviewer", email="concurrent-index@example.test", password_hash="synthetic"))
        db.add(Investigation(id=investigation_id, case_number="SYN-REL-002", title="Synthetic concurrency test", created_by_user_id=user_id))
        await db.flush()
        doc = Document(id=document_id, investigation_id=investigation_id, filename="synthetic.pdf", content_type="application/pdf", size_bytes=1, sha256="a" * 64)
        db.add(doc)
        await db.commit()

        async def refresh():
            async with app.state.test_sessions() as session:
                await persist_document_mentions_and_candidates(
                    session, investigation_id=investigation_id, document_id=document_id,
                    filename="synthetic.pdf", source_sha256="a" * 64,
                    blocks=[{"block_id": "b1", "page_index": 0, "text": "Email: same@example.test"}],
                )
                await session.commit()

        await asyncio.gather(refresh(), refresh())
        async with app.state.test_sessions() as db:
            count = await db.scalar(select(func.count(DeterministicEvidenceMention.id)).where(DeterministicEvidenceMention.document_id == document_id))
            assert count == 1


@pytest.mark.asyncio
async def test_candidate_creation_skips_conflicting_legacy_review_and_fabricated_history(client):
    user_id, investigation_id = uuid.uuid4(), uuid.uuid4()
    app = __import__("app.main", fromlist=["app"]).app
    document_ids = [uuid.uuid4(), uuid.uuid4()]
    async with app.state.test_sessions() as db:
        db.add(User(id=user_id, full_name="Synthetic Reviewer", email="legacy-conflict@example.test", password_hash="synthetic"))
        db.add(Investigation(
            id=investigation_id, case_number="SYN-REL-003", title="Synthetic legacy review conflict",
            created_by_user_id=user_id,
        ))
        await db.flush()
        documents = [Document(
            id=document_id, investigation_id=investigation_id, filename=f"legacy-{index}.pdf",
            content_type="application/pdf", size_bytes=1, sha256=f"{index + 1:064x}",
        ) for index, document_id in enumerate(document_ids)]
        db.add_all(documents)
        db.add_all([
            DerivedGraphNode(
                investigation_id=investigation_id, canonical_id="det:identifier:invoice:INV-12345:confirmed",
                label="INV-12345", entity_type="INVOICE", document_ids=[str(value) for value in document_ids],
                resolution="identifier_match", basis="Synthetic legacy source", review_status="confirmed",
                reviewed_by_user_id=user_id, reviewed_at=datetime.now(timezone.utc), review_reason="Confirmed synthetic",
            ),
            DerivedGraphNode(
                investigation_id=investigation_id, canonical_id="det:identifier:invoice:INV-12345:rejected",
                label="INV-12345", entity_type="INVOICE", document_ids=[str(value) for value in document_ids],
                resolution="identifier_match", basis="Synthetic legacy source", review_status="rejected",
                reviewed_by_user_id=user_id, reviewed_at=datetime.now(timezone.utc), review_reason="Rejected synthetic",
            ),
        ])
        await db.commit()

    for index, document in enumerate(documents):
        async with app.state.test_sessions() as db:
            await persist_document_mentions_and_candidates(
                db, investigation_id=investigation_id, document_id=document.id,
                filename=document.filename, source_sha256=document.sha256,
                blocks=[{"block_id": f"legacy-{index}", "page_index": 0, "text": "Invoice: INV-12345"}],
            )
            await db.commit()

    async with app.state.test_sessions() as db:
        candidate = await db.scalar(select(RelationshipCandidate).where(
            RelationshipCandidate.investigation_id == investigation_id,
            RelationshipCandidate.candidate_key == _candidate_key(investigation_id, "INVOICE", "INV-12345"),
        ))
        assert candidate is not None
        assert candidate.review_status == "pending"
        assert candidate.reviewed_by_user_id is None
        assert candidate.reviewed_at is None
        assert candidate.review_reason is None
        assert candidate.review_history == []

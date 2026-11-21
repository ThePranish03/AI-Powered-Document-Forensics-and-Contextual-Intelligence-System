import json
import sqlite3
import uuid
from collections import defaultdict
from datetime import datetime, timezone

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker

from app.db.base import Base
from app.models.entities import (
    DeterministicEvidenceMention,
    Document,
    EvidenceSpan,
    ExtractionRun,
    Investigation,
    OCRResult,
    RelationshipCandidate,
    User,
)
from app.services.relationship_backfill import (
    BackfillError,
    ConcurrentBackfillError,
    EXPECTED_MATCHER_VERSION,
    EXPECTED_MENTION_COUNT,
    EXPECTED_CANDIDATE_GROUP_COUNT,
    EXPECTED_REVISION,
    FIXED_MANIFEST,
    _candidate_key,
    _exclusive_lock,
    run_backfill,
)


def _make_source_blocks(manifest=FIXED_MANIFEST, *, singleton_candidate=False):
    by_investigation = defaultdict(list)
    for entry in manifest:
        by_investigation[entry.investigation_id].append(entry)
    mentions_by_document = defaultdict(list)
    for _investigation_id, entries in by_investigation.items():
        for index in range(6):
            value = f"SHARED-{index:02d}"
            for entry in entries[:2]:
                mentions_by_document[entry.document_id].append(value)
    if singleton_candidate:
        mentions_by_document[manifest[0].document_id].append("SINGLETON-STALE-001")

    capacity = {entry.document_id: entry.source_block_count for entry in manifest}
    serial = 0
    assigned = sum(map(len, mentions_by_document.values()))
    target_solos = EXPECTED_MENTION_COUNT - assigned
    for _ in range(target_solos):
        while True:
            entry = manifest[serial % len(manifest)]
            serial += 1
            if len(mentions_by_document[entry.document_id]) < capacity[entry.document_id]:
                mentions_by_document[entry.document_id].append(f"SOLO-{serial:05d}")
                break

    result = {}
    for entry in manifest:
        assigned_values = iter(mentions_by_document[entry.document_id])
        blocks = []
        for index in range(entry.source_block_count):
            value = next(assigned_values, None)
            text = f"Invoice: {value}" if value else "No identifier in this source block."
            blocks.append({
                "block_id": f"{entry.document_id[:8]}-b{index:03d}",
                "page_index": index // 10,
                "extraction_method": "ocr",
                "text": text,
            })
        result[entry.document_id] = blocks
    assert sum(len(values) for values in mentions_by_document.values()) == EXPECTED_MENTION_COUNT
    return result


def _seed_database(path, *, revision=EXPECTED_REVISION, alter=None, singleton_candidate=False):
    engine = create_engine(f"sqlite:///{path.as_posix()}")

    @event.listens_for(engine,"connect")
    def _enable_foreign_keys(dbapi_connection,_connection_record):
        cursor=dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    Base.metadata.create_all(engine)
    sessions = sessionmaker(engine,expire_on_commit=False)
    blocks_by_doc = _make_source_blocks(singleton_candidate=singleton_candidate)
    user_id = uuid.uuid4()
    with sessions() as session:
        session.add(User(
            id=user_id, full_name="Backfill Fixture", email="backfill-fixture@example.test",
            password_hash="not-a-secret",
        ))
        session.flush()
        investigations = {}
        for entry in FIXED_MANIFEST:
            if entry.investigation_id not in investigations:
                investigation = Investigation(
                    id=uuid.UUID(entry.investigation_id), case_number=entry.case_number,
                    title=f"Fixture {entry.case_number}", created_by_user_id=user_id,
                )
                investigations[entry.investigation_id] = investigation
                session.add(investigation)
        session.flush()
        for entry in FIXED_MANIFEST:
            blocks = blocks_by_doc[entry.document_id]
            if alter:
                alter(entry, blocks)
            doc_id = uuid.UUID(entry.document_id)
            session.add(Document(
                id=doc_id, investigation_id=uuid.UUID(entry.investigation_id),
                filename=entry.filename, content_type="application/pdf", size_bytes=1,
                sha256=entry.source_sha256, processing_status="ocr_completed",
            ))
        session.flush()
        for entry in FIXED_MANIFEST:
            doc_id = uuid.UUID(entry.document_id)
            blocks = blocks_by_doc[entry.document_id]
            session.add(OCRResult(
                id=uuid.UUID(entry.ocr_result_id), document_id=doc_id, provider="synthetic-fixture",
                raw_result={}, blocks=blocks, recognized_text="\n".join(b["text"] for b in blocks),
            ))
            extraction = ExtractionRun(
                document_id=doc_id, run_number=1, provider="synthetic-fixture",
                status="completed", configuration={},
            )
            session.add(extraction)
            session.flush()
            for block in blocks:
                session.add(EvidenceSpan(
                    document_id=doc_id, extraction_run_id=extraction.id,
                    block_id=block["block_id"], text=block["text"],
                    extraction_method=block["extraction_method"], source_sha256=entry.source_sha256,
                    source_reference=block["block_id"],
                ))
        session.commit()
    engine.dispose()
    with sqlite3.connect(path) as connection:
        connection.execute("CREATE TABLE alembic_version (version_num VARCHAR(32) NOT NULL PRIMARY KEY)")
        connection.execute("INSERT INTO alembic_version(version_num) VALUES(?)", (revision,))
        connection.commit()
    return blocks_by_doc


@pytest.fixture
def fixture_database(tmp_path):
    path = tmp_path / "isolated-backfill.sqlite3"
    _seed_database(path)
    return path


def _run_kwargs(path, tmp_path, **kwargs):
    return {
        "database_path": path,
        "expected_database_path": path,
        "state_database": tmp_path / "state" / "ledger.sqlite3",
        **kwargs,
    }


def test_dry_run_reports_fixed_totals_without_writes(fixture_database, tmp_path):
    before = fixture_database.read_bytes()
    report = run_backfill(**_run_kwargs(fixture_database, tmp_path))
    assert report["status"] == "dry_run"
    assert report["source_blocks_validated"] == 532
    assert report["mentions_inserted"] == EXPECTED_MENTION_COUNT
    assert report["candidate_groups_created"] == EXPECTED_CANDIDATE_GROUP_COUNT
    assert report["documents"]["Pending"] == 21
    assert fixture_database.read_bytes() == before
    assert not (tmp_path / "state" / "ledger.sqlite3").exists()
    with sqlite3.connect(fixture_database) as connection:
        assert connection.execute("SELECT count(*) FROM deterministic_evidence_mentions").fetchone()[0] == 0
        assert connection.execute("SELECT count(*) FROM relationship_candidates").fetchone()[0] == 0


def test_execute_flag_is_required_for_persistence(fixture_database, tmp_path):
    report = run_backfill(**_run_kwargs(fixture_database, tmp_path))
    assert report["status"] == "dry_run"
    with sqlite3.connect(fixture_database) as connection:
        assert connection.execute("SELECT count(*) FROM deterministic_evidence_mentions").fetchone()[0] == 0


def test_wrong_database_path_is_rejected(fixture_database, tmp_path):
    other = tmp_path / "other.sqlite3"
    other.touch()
    with pytest.raises(BackfillError, match="unexpected database path"):
        run_backfill(database_path=other, expected_database_path=fixture_database)


def test_wrong_revision_is_rejected(fixture_database, tmp_path):
    with sqlite3.connect(fixture_database) as connection:
        connection.execute("UPDATE alembic_version SET version_num='0014_processing_error_diagnostics'")
    with pytest.raises(BackfillError, match="Database revision"):
        run_backfill(**_run_kwargs(fixture_database, tmp_path))


def test_wrong_required_matcher_version_is_rejected(fixture_database, tmp_path):
    with pytest.raises(BackfillError, match="matcher version"):
        run_backfill(**_run_kwargs(fixture_database, tmp_path, expected_matcher_version="other"))


def test_changed_stored_document_hash_is_rejected(fixture_database, tmp_path):
    with sqlite3.connect(fixture_database) as connection:
        connection.execute("UPDATE documents SET sha256=? WHERE id=?", ("f" * 64, FIXED_MANIFEST[0].document_id))
    with pytest.raises(BackfillError, match="identity or stored hash changed"):
        run_backfill(**_run_kwargs(fixture_database, tmp_path))


def test_changed_ocr_result_id_is_rejected(fixture_database, tmp_path):
    with sqlite3.connect(fixture_database) as connection:
        connection.execute("UPDATE ocr_results SET id=? WHERE document_id=?", (uuid.uuid4().hex, FIXED_MANIFEST[0].document_id))
    with pytest.raises(BackfillError, match="OCR result identity changed"):
        run_backfill(**_run_kwargs(fixture_database, tmp_path))


def test_changed_stored_block_count_is_rejected(fixture_database, tmp_path):
    entry=FIXED_MANIFEST[0]
    with sqlite3.connect(fixture_database) as connection:
        blocks=connection.execute("SELECT blocks FROM ocr_results WHERE document_id=?",(entry.document_id,)).fetchone()[0]
        values=json.loads(blocks)
        values.pop()
        connection.execute("UPDATE ocr_results SET blocks=? WHERE document_id=?",(json.dumps(values),entry.document_id))
    with pytest.raises(BackfillError,match="OCR block count changed"):
        run_backfill(**_run_kwargs(fixture_database,tmp_path))


@pytest.mark.parametrize("corruption", ["missing", "text", "method", "hash"])
def test_missing_or_mismatched_span_provenance_is_rejected(fixture_database, tmp_path, corruption):
    entry = FIXED_MANIFEST[0]
    with sqlite3.connect(fixture_database) as connection:
        if corruption == "missing":
            connection.execute(
                "DELETE FROM evidence_spans WHERE rowid=(SELECT rowid FROM evidence_spans WHERE document_id=? LIMIT 1)",
                (entry.document_id,),
            )
        elif corruption == "text":
            connection.execute("UPDATE evidence_spans SET text='altered' WHERE document_id=?", (entry.document_id,))
        elif corruption == "method":
            connection.execute("UPDATE evidence_spans SET extraction_method='native' WHERE document_id=?", (entry.document_id,))
        else:
            connection.execute("UPDATE evidence_spans SET source_sha256=? WHERE document_id=?", ("0" * 64, entry.document_id))
    with pytest.raises(BackfillError, match="EvidenceSpan"):
        run_backfill(**_run_kwargs(fixture_database, tmp_path))


def test_all_manifest_entries_process_in_isolated_fixture_with_expected_totals(fixture_database, tmp_path):
    report = run_backfill(**_run_kwargs(fixture_database, tmp_path, execute=True))
    assert report["status"] == "completed", json.dumps(report,indent=2)
    assert report["documents"]["Completed"] == 21
    assert report["source_blocks_validated"] == 532
    assert report["mentions_inserted"] == 400
    assert report["candidate_groups_created"] == 24
    assert report["integrity_check"] == "ok"
    assert report["foreign_key_violations"] == 0
    with sqlite3.connect(fixture_database) as connection:
        assert connection.execute("SELECT count(*) FROM deterministic_evidence_mentions WHERE is_current=1").fetchone()[0] == 400
        assert connection.execute("SELECT count(*) FROM relationship_candidates").fetchone()[0] == 24


def test_completed_document_retry_has_no_duplicate_occurrences_or_candidates(fixture_database, tmp_path):
    state = tmp_path / "state" / "ledger.sqlite3"
    run_id = str(uuid.uuid4())
    first = run_backfill(**_run_kwargs(fixture_database, tmp_path, execute=True, run_id=run_id))
    second = run_backfill(**_run_kwargs(fixture_database, tmp_path, execute=True, run_id=run_id))
    assert first["status"] == second["status"] == "completed"
    assert second["mentions_inserted"] == 0
    assert second["mentions_skipped"] == 400
    assert second["candidate_groups_created"] == 0
    with sqlite3.connect(fixture_database) as connection:
        assert connection.execute("SELECT count(*) FROM deterministic_evidence_mentions").fetchone()[0] == 400
        assert connection.execute("SELECT count(*) FROM relationship_candidates").fetchone()[0] == 24
        assert connection.execute("SELECT count(*) FROM audit_chain_events").fetchone()[0] == 0


def test_interruption_after_data_commit_is_reconciled_without_duplicates(fixture_database, tmp_path):
    run_id = str(uuid.uuid4())
    seen = set()

    class SimulatedCrash(BaseException):
        pass

    def crash_after_first(document_id):
        if document_id not in seen:
            seen.add(document_id)
            raise SimulatedCrash()

    with pytest.raises(SimulatedCrash):
        run_backfill(**_run_kwargs(
            fixture_database,tmp_path,execute=True,run_id=run_id,
            _after_document_commit=crash_after_first,
        ))
    resumed = run_backfill(**_run_kwargs(fixture_database,tmp_path,execute=True,run_id=run_id))
    assert resumed["status"] == "completed"
    assert resumed["reconciliation_count"] == 1
    with sqlite3.connect(fixture_database) as connection:
        assert connection.execute("SELECT count(*) FROM deterministic_evidence_mentions").fetchone()[0] == 400
        assert connection.execute("SELECT count(*) FROM relationship_candidates").fetchone()[0] == 24
        assert connection.execute("SELECT count(*) FROM audit_chain_events").fetchone()[0] == 0


def test_overlapping_runner_invocation_is_refused(fixture_database, tmp_path):
    state = tmp_path / "state" / "ledger.sqlite3"
    with _exclusive_lock(state):
        with pytest.raises(ConcurrentBackfillError):
            run_backfill(**_run_kwargs(fixture_database,tmp_path,execute=True))
    with sqlite3.connect(fixture_database) as connection:
        assert connection.execute("SELECT count(*) FROM deterministic_evidence_mentions").fetchone()[0] == 0


def test_existing_candidate_review_identity_and_history_survive_refresh(fixture_database, tmp_path):
    entry = FIXED_MANIFEST[0]
    value = "SHARED-00"
    reviewer = uuid.uuid4()
    def seed_candidate():
        engine = create_engine(f"sqlite:///{fixture_database.as_posix()}")
        sessions = sessionmaker(engine,expire_on_commit=False)
        with sessions() as session:
            user = User(id=reviewer,full_name="Reviewer",email="reviewer@example.test",password_hash="x")
            session.add(user)
            session.flush()
            session.add(RelationshipCandidate(
                id=uuid.uuid4(), investigation_id=uuid.UUID(entry.investigation_id),
                candidate_key=_candidate_key(entry.investigation_id,"INVOICE",value),
                evidence_type="INVOICE",normalized_value=value,lifecycle_status="stale",
                review_status="confirmed",document_ids=[],evidence_refs=[],explanation="existing",
                reviewed_by_user_id=reviewer,reviewed_at=datetime.now(timezone.utc),
                review_reason="Verified before backfill",review_history=[{"status":"confirmed","reason":"prior"}],
            ))
            session.commit()
        engine.dispose()
    seed_candidate()
    report=run_backfill(**_run_kwargs(fixture_database,tmp_path,execute=True))
    assert report["status"]=="completed"
    with sqlite3.connect(fixture_database) as connection:
        row=connection.execute("SELECT lifecycle_status,review_status,reviewed_by_user_id,review_reason,review_history FROM relationship_candidates WHERE candidate_key=?",(_candidate_key(entry.investigation_id,"INVOICE",value),)).fetchone()
    assert row[0]=="active"
    assert row[1]=="confirmed"
    assert row[2]==reviewer.hex
    assert row[3]=="Verified before backfill"
    assert json.loads(row[4])==[{"status":"confirmed","reason":"prior"}]


def test_single_document_candidate_becomes_stale_without_changing_review(tmp_path):
    # A preexisting candidate is refreshed by the ordinary persistence function
    # when its only current source occurrence is present.
    entry=FIXED_MANIFEST[0]
    value="SINGLETON-STALE-001"
    reviewer=uuid.uuid4()
    fixture_database=tmp_path / "singleton-backfill.sqlite3"
    _seed_database(fixture_database,singleton_candidate=True)
    # Rebuild this fixture with one singleton invoice and a candidate already
    # indexed in the same investigation; all 400 occurrence and 24 group totals
    # remain pinned.
    _seed_candidate_for_singleton(fixture_database,entry,value,reviewer)
    report=run_backfill(**_run_kwargs(fixture_database,tmp_path,execute=True))
    assert report["status"]=="completed"
    with sqlite3.connect(fixture_database) as connection:
        row=connection.execute("SELECT lifecycle_status,review_status,reviewed_by_user_id FROM relationship_candidates WHERE candidate_key=?",(_candidate_key(entry.investigation_id,"INVOICE",value),)).fetchone()
    assert row==("stale","rejected",reviewer.hex)


def _seed_candidate_for_singleton(path,entry,value,reviewer):
    engine=create_engine(f"sqlite:///{path.as_posix()}")
    sessions=sessionmaker(engine,expire_on_commit=False)
    with sessions() as session:
        session.add(User(id=reviewer,full_name="Reviewer",email="stale-reviewer@example.test",password_hash="x"))
        session.flush()
        session.add(RelationshipCandidate(
            id=uuid.uuid4(),investigation_id=uuid.UUID(entry.investigation_id),
            candidate_key=_candidate_key(entry.investigation_id,"INVOICE",value),
            evidence_type="INVOICE",normalized_value=value,lifecycle_status="active",
            review_status="rejected",document_ids=[],evidence_refs=[],explanation="preexisting",
            reviewed_by_user_id=reviewer,review_reason="Rejected before refresh",
            review_history=[{"status":"rejected","reason":"prior"}],
        ))
        session.commit()
    engine.dispose()


def test_candidate_key_is_investigation_scoped(fixture_database, tmp_path):
    report=run_backfill(**_run_kwargs(fixture_database,tmp_path,execute=True))
    assert report["candidate_groups_created"]==24
    with sqlite3.connect(fixture_database) as connection:
        values=connection.execute("SELECT candidate_key,investigation_id FROM relationship_candidates WHERE evidence_type='INVOICE' AND normalized_value='SHARED-00'").fetchall()
    assert len(values)==4
    assert len({row[0] for row in values})==4
    assert len({row[1] for row in values})==4


def test_simulated_failure_is_recorded_then_resumed_safely(fixture_database, tmp_path):
    run_id=str(uuid.uuid4())
    first_id=FIXED_MANIFEST[0].document_id
    failed=set()
    def fail_once(document_id):
        if document_id==first_id and document_id not in failed:
            failed.add(document_id)
            raise RuntimeError("synthetic failure")
    first=run_backfill(**_run_kwargs(
        fixture_database,tmp_path,execute=True,run_id=run_id,_before_document=fail_once,
    ))
    assert first["status"]=="failed"
    assert first["documents"]["Failed"]==1
    second=run_backfill(**_run_kwargs(fixture_database,tmp_path,execute=True,run_id=run_id))
    assert second["status"]=="completed"
    assert second["retry_count"]>=1
    assert second["documents"]["Completed"]==21


def test_manifest_and_matcher_are_fixed():
    assert len(FIXED_MANIFEST)==21
    assert sum(row.source_block_count for row in FIXED_MANIFEST)==532
    assert all(row.matcher_version==EXPECTED_MATCHER_VERSION for row in FIXED_MANIFEST)
    assert EXPECTED_REVISION=="0015_incremental_relationship_intelligence"
    target=next(row for row in FIXED_MANIFEST if row.document_id=="9a8601cc956147d8bb08d92baf634ef1")
    assert target.ocr_result_id=="4200a36a723249fab94a4c5d5c155f27"


def test_runner_has_no_document_worker_or_provider_dependencies():
    from pathlib import Path
    source=Path(__import__("app.services.relationship_backfill",fromlist=["__file__"]).__file__).read_text(encoding="utf-8")
    assert "ocr_worker" not in source
    assert "llm_client" not in source
    assert "pdf_forensics" not in source
    assert "storage_key" not in source


def test_failure_does_not_report_success_when_document_remains_failed(fixture_database, tmp_path):
    def always_fail(document_id):
        if document_id==FIXED_MANIFEST[0].document_id:
            raise RuntimeError("still failing")
    report=run_backfill(**_run_kwargs(fixture_database,tmp_path,execute=True,_before_document=always_fail))
    assert report["status"]=="failed"
    assert report["documents"]["Failed"]==1


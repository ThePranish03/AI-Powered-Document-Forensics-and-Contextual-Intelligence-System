"""Safe, fixed-manifest historical relationship-index backfill.

The canonical application database is always resolved beside this package's
backend directory. Run state is kept in a separate SQLite sidecar so this
feature does not require a schema migration at revision 0015. Because the
sidecar and application database cannot share an atomic SQLite transaction,
an interrupted Processing row is reconciled against persisted mention and
candidate state before any retry is attempted.
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import sqlite3
import time
import uuid
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Iterable

from sqlalchemy import event, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.models.entities import (
    DeterministicEvidenceMention,
    Document,
    EvidenceSpan,
    OCRResult,
    RelationshipCandidate,
)
from app.services.relationship_index import (
    MATCHER_VERSION,
    _candidate_key,
    extract_mentions,
    persist_document_mentions_and_candidates,
)


EXPECTED_REVISION = "0015_incremental_relationship_intelligence"
EXPECTED_MATCHER_VERSION = "deterministic_mentions_v3"
EXPECTED_MENTION_COUNT = 400
EXPECTED_CANDIDATE_GROUP_COUNT = 24
BACKEND_DIR = Path(__file__).resolve().parents[2]
REPOSITORY_DIR = BACKEND_DIR.parent
CANONICAL_DATABASE = (BACKEND_DIR / "docforensiq.db").resolve()
DEFAULT_STATE_DATABASE = REPOSITORY_DIR / "work" / "backfill_state" / "relationship_backfill.sqlite3"


@dataclass(frozen=True)
class ManifestEntry:
    case_number: str
    investigation_id: str
    document_id: str
    filename: str
    source_sha256: str
    ocr_result_id: str
    source_block_count: int
    evidence_span_count: int
    matcher_version: str = EXPECTED_MATCHER_VERSION


# This manifest is deliberately explicit. Do not replace it with a database
# query that discovers documents dynamically.
FIXED_MANIFEST: tuple[ManifestEntry, ...] = (
    ManifestEntry("DF-4A69647434B8", "14a5dfe73a114e2ea8d318f65d70a37c", "30561cf8caa04e31a48e36ed7051bd4f", "doc1_corporate_registry_ownership.pdf", "cd0957542726c9f942b7ed3531a700eb1c652e99928103745aa845480f0187dc", "0952a4b5b0334dffb537a60e99f0b0ee", 9, 9),
    ManifestEntry("DF-4A69647434B8", "14a5dfe73a114e2ea8d318f65d70a37c", "4cab8ee2f7ce48e390015c525ed7ff70", "doc2_financial_wire_ledger.pdf", "ab5b363687e3b994db8a60fbbc15ae2043ee04559b4443d655e7a2a7eb058b82", "cda351867a10457b9facbf1a1ce3ffac", 9, 9),
    ManifestEntry("DF-4A69647434B8", "14a5dfe73a114e2ea8d318f65d70a37c", "02ab7c8a1db140ea880feeeaa015b5fe", "doc3_communications_access_logs.pdf", "1ead368e65a5190a5b37aad1768a0acef6ff79b14edbb946d4df58e520824949", "bdd6351cc6f94721820cad1288684a64", 10, 10),
    ManifestEntry("DF-735334F3549A", "64ba782c3aad43a4a757f9d31b6e9865", "c438c475365f4b89a4aafbe8b1ed2e2d", "doc1_corporate_registry_ownership.pdf", "cd0957542726c9f942b7ed3531a700eb1c652e99928103745aa845480f0187dc", "bf6fe868107a417b9497329aa9daacad", 25, 25),
    ManifestEntry("DF-735334F3549A", "64ba782c3aad43a4a757f9d31b6e9865", "de3fe584b2734235aa32b2a4031332a8", "doc2_financial_wire_ledger.pdf", "ab5b363687e3b994db8a60fbbc15ae2043ee04559b4443d655e7a2a7eb058b82", "4db6d79a291f42d48bf501c5cd05afa3", 20, 20),
    ManifestEntry("DF-735334F3549A", "64ba782c3aad43a4a757f9d31b6e9865", "c83a01a3b32a4780a8f0bdf59cd9bb54", "doc3_communications_access_logs.pdf", "1ead368e65a5190a5b37aad1768a0acef6ff79b14edbb946d4df58e520824949", "b975bcb550514eada3c884ec31a84e0a", 25, 25),
    ManifestEntry("DF-735334F3549A", "64ba782c3aad43a4a757f9d31b6e9865", "f3d168eafb884cfd99a6cbdbbebda4c6", "doc4_customs_cargo_manifest.pdf", "4dd3eec9e153be6659fa270a389d6bf121626a36158412d766f1b32b610c9e69", "050d33700b1345a4a1fb7ddcc7b5fb27", 33, 33),
    ManifestEntry("DF-735334F3549A", "64ba782c3aad43a4a757f9d31b6e9865", "e9dd3f2c2e024a6591cef09d26672858", "doc5_hr_compliance_dossier.pdf", "a5928427a09770273c4e024bd539c27858fd5d3b067a23767ace0970cae277d4", "a277f8a1eaf44dd290a21673513cded4", 30, 30),
    ManifestEntry("DF-735334F3549A", "64ba782c3aad43a4a757f9d31b6e9865", "045f8d847c834672ae959aab7a1d29c9", "doc6_witness_deposition_lease.pdf", "10615361ad12232df299c0823056ea9f8dbcb010765c79566064dc85e28d7c4f", "3914c581d7a94bd38f7750c8b55a7123", 35, 35),
    ManifestEntry("DF-9D0256F549B2", "254e46e0a57f454f889c6e617ca0d59a", "1f3e73dd32db49a38d40bbfb33aae3da", "doc1_corporate_registry_ownership.pdf", "cd0957542726c9f942b7ed3531a700eb1c652e99928103745aa845480f0187dc", "3912aa6569c44f5da1caac5d2ba6143f", 25, 25),
    ManifestEntry("DF-9D0256F549B2", "254e46e0a57f454f889c6e617ca0d59a", "2b7d5774685d4557922df3a4a3c92cfa", "doc2_financial_wire_ledger.pdf", "ab5b363687e3b994db8a60fbbc15ae2043ee04559b4443d655e7a2a7eb058b82", "c2da1c582ebc4fd29b32a2cada369511", 20, 20),
    ManifestEntry("DF-9D0256F549B2", "254e46e0a57f454f889c6e617ca0d59a", "5a617d70dc884c57b536d9d72b659091", "doc3_communications_access_logs.pdf", "1ead368e65a5190a5b37aad1768a0acef6ff79b14edbb946d4df58e520824949", "d13f6c1e93a14a3aae2128a9dc138dc4", 25, 25),
    ManifestEntry("DF-9D0256F549B2", "254e46e0a57f454f889c6e617ca0d59a", "92b0fbd884554daf9efb3b575f760a0c", "doc4_customs_cargo_manifest.pdf", "4dd3eec9e153be6659fa270a389d6bf121626a36158412d766f1b32b610c9e69", "6005526bc9264d72a36cdf8308873766", 33, 33),
    ManifestEntry("DF-9D0256F549B2", "254e46e0a57f454f889c6e617ca0d59a", "829a1de792154e789667314978f553f5", "doc5_hr_compliance_dossier.pdf", "a5928427a09770273c4e024bd539c27858fd5d3b067a23767ace0970cae277d4", "3e03ccc7a70b41a3975de00230477406", 30, 30),
    ManifestEntry("DF-9D0256F549B2", "254e46e0a57f454f889c6e617ca0d59a", "ff1f5b96f63f4c148a7f469c66df8707", "doc6_witness_deposition_lease.pdf", "10615361ad12232df299c0823056ea9f8dbcb010765c79566064dc85e28d7c4f", "83908dd0584242179a5e946abcdd17cc", 35, 35),
    ManifestEntry("DF-C106A44EF5CA", "ae8a78f645f4434c9b2c1552948865b3", "6fe790ee0a5e4d6590309fe7c45425ad", "doc1_corporate_registry_ownership.pdf", "cd0957542726c9f942b7ed3531a700eb1c652e99928103745aa845480f0187dc", "0f762cf3ba464409a9f491cf894d4898", 25, 25),
    ManifestEntry("DF-C106A44EF5CA", "ae8a78f645f4434c9b2c1552948865b3", "f3ed97a05dd8408788bf4fbf508b4a2a", "doc2_financial_wire_ledger.pdf", "ab5b363687e3b994db8a60fbbc15ae2043ee04559b4443d655e7a2a7eb058b82", "c944253bbce54f2bb6d3d96c567b57ee", 20, 20),
    ManifestEntry("DF-C106A44EF5CA", "ae8a78f645f4434c9b2c1552948865b3", "5f2cbf2104cf461bb17aebdf7e5264af", "doc3_communications_access_logs.pdf", "1ead368e65a5190a5b37aad1768a0acef6ff79b14edbb946d4df58e520824949", "8edc98b721d541e5b71e5c5bf37337e0", 25, 25),
    ManifestEntry("DF-C106A44EF5CA", "ae8a78f645f4434c9b2c1552948865b3", "f7f5bc0476184892b18ab70bf83bd0c3", "doc4_customs_cargo_manifest.pdf", "4dd3eec9e153be6659fa270a389d6bf121626a36158412d766f1b32b610c9e69", "342bccac212b4d8782584bae61c8e7ac", 33, 33),
    ManifestEntry("DF-C106A44EF5CA", "ae8a78f645f4434c9b2c1552948865b3", "a97ed532da49435b963da97071435bb0", "doc5_hr_compliance_dossier.pdf", "a5928427a09770273c4e024bd539c27858fd5d3b067a23767ace0970cae277d4", "5be49a028c4b40c8a21969386a894660", 30, 30),
    ManifestEntry("DF-C106A44EF5CA", "ae8a78f645f4434c9b2c1552948865b3", "9a8601cc956147d8bb08d92baf634ef1", "doc6_witness_deposition_lease.pdf", "10615361ad12232df299c0823056ea9f8dbcb010765c79566064dc85e28d7c4f", "4200a36a723249fab94a4c5d5c155f27", 35, 35),
)


class BackfillError(RuntimeError):
    """The fixed backfill cannot proceed safely."""


class ConcurrentBackfillError(BackfillError):
    """Another runner currently holds the execution lock."""


@dataclass
class ValidatedDocument:
    entry: ManifestEntry
    blocks: list[dict]
    mentions: list[dict]
    validation_seconds: float = 0.0


def _manifest_hash(manifest: Iterable[ManifestEntry] = FIXED_MANIFEST) -> str:
    payload = [asdict(row) for row in manifest]
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _resolve_expected_path(database_path: Path | str, expected_database_path: Path | str) -> Path:
    actual = Path(database_path).absolute()
    expected = Path(expected_database_path).absolute()
    if os.path.normcase(str(actual)) != os.path.normcase(str(expected)):
        raise BackfillError(f"Refusing unexpected database path: {actual}")
    if not actual.is_file():
        raise BackfillError("Expected database path is not a file")
    return actual


def _readonly_connection(path: Path) -> sqlite3.Connection:
    connection = sqlite3.connect(path.as_uri() + "?mode=ro", uri=True, timeout=30)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA query_only=ON")
    if connection.execute("PRAGMA query_only").fetchone()[0] != 1:
        connection.close()
        raise BackfillError("SQLite query_only could not be enabled")
    return connection


def _validate_manifest_entry(connection: sqlite3.Connection, entry: ManifestEntry) -> ValidatedDocument:
    validation_started = time.perf_counter()
    if entry.matcher_version != EXPECTED_MATCHER_VERSION or MATCHER_VERSION != EXPECTED_MATCHER_VERSION:
        raise BackfillError("Manifest or installed matcher version is not deterministic_mentions_v3")
    doc = connection.execute(
        """SELECT d.id,d.investigation_id,d.filename,d.sha256,i.case_number
           FROM documents d JOIN investigations i ON i.id=d.investigation_id
           WHERE d.id=?""", (entry.document_id,),
    ).fetchone()
    if doc is None:
        raise BackfillError(f"Manifest document is missing: {entry.document_id}")
    actual_doc = (doc["case_number"], doc["investigation_id"], doc["filename"], doc["sha256"])
    expected_doc = (entry.case_number, entry.investigation_id, entry.filename, entry.source_sha256)
    if actual_doc != expected_doc:
        raise BackfillError(f"Document identity or stored hash changed: {entry.document_id}")

    ocr_rows = connection.execute(
        "SELECT id,blocks FROM ocr_results WHERE document_id=?", (entry.document_id,),
    ).fetchall()
    if len(ocr_rows) != 1 or ocr_rows[0]["id"] != entry.ocr_result_id:
        raise BackfillError(f"OCR result identity changed: {entry.document_id}")
    try:
        blocks = json.loads(ocr_rows[0]["blocks"]) if isinstance(ocr_rows[0]["blocks"], str) else ocr_rows[0]["blocks"]
    except (TypeError, json.JSONDecodeError) as exc:
        raise BackfillError(f"Stored OCR blocks are invalid JSON: {entry.document_id}") from exc
    if not isinstance(blocks, list) or len(blocks) != entry.source_block_count:
        raise BackfillError(f"Stored OCR block count changed: {entry.document_id}")

    spans = connection.execute(
        """SELECT block_id,text,extraction_method,source_sha256
           FROM evidence_spans WHERE document_id=?""", (entry.document_id,),
    ).fetchall()
    _validate_block_span_provenance(entry,blocks,spans)
    mentions = extract_mentions(
        investigation_id=entry.investigation_id,
        document_id=entry.document_id,
        filename=entry.filename,
        source_sha256=entry.source_sha256,
        blocks=blocks,
    )
    return ValidatedDocument(
        entry=entry,blocks=blocks,mentions=mentions,
        validation_seconds=round(time.perf_counter()-validation_started,4),
    )


def _validate_block_span_provenance(entry: ManifestEntry, blocks: list[dict], spans: Iterable) -> None:
    spans = list(spans)
    if len(spans) != entry.evidence_span_count:
        raise BackfillError(f"EvidenceSpan count changed: {entry.document_id}")
    by_id: dict[str, list[sqlite3.Row]] = {}
    for span in spans:
        if isinstance(span, dict):
            record = span
        else:
            record = {key: span[key] for key in ("block_id","text","extraction_method","source_sha256")}
        by_id.setdefault(str(record["block_id"]), []).append(record)
    seen: set[str] = set()
    for block in blocks:
        if not isinstance(block, dict):
            raise BackfillError(f"Stored OCR block is not an object: {entry.document_id}")
        block_id = str(block.get("block_id") or "")
        if not block_id or block_id in seen:
            raise BackfillError(f"Stored OCR block IDs are missing or duplicated: {entry.document_id}")
        seen.add(block_id)
        text = block.get("text")
        method = str(block.get("extraction_method") or "ocr")
        matches = [
            span for span in by_id.get(block_id, [])
            if span["text"] == text
            and span["extraction_method"] == method
            and span["source_sha256"] == entry.source_sha256
        ]
        if len(matches) != 1:
            raise BackfillError(f"EvidenceSpan provenance changed for {entry.document_id}/{block_id}")
    if set(by_id) != seen:
        raise BackfillError(f"EvidenceSpan block IDs do not exactly match OCR blocks: {entry.document_id}")


def _validate_database(path: Path, expected_revision: str, expected_matcher_version: str) -> tuple[str, dict[str, ValidatedDocument]]:
    if expected_matcher_version != EXPECTED_MATCHER_VERSION or MATCHER_VERSION != EXPECTED_MATCHER_VERSION:
        raise BackfillError("Installed or required matcher version is not deterministic_mentions_v3")
    if expected_revision != EXPECTED_REVISION:
        raise BackfillError(f"Runner requires Alembic revision {EXPECTED_REVISION}")
    connection = _readonly_connection(path)
    try:
        integrity = [tuple(row) for row in connection.execute("PRAGMA integrity_check").fetchall()]
        if integrity != [("ok",)]:
            raise BackfillError(f"Database integrity check failed: {integrity}")
        fk = connection.execute("PRAGMA foreign_key_check").fetchall()
        if fk:
            raise BackfillError(f"Database foreign-key check failed: {len(fk)} violation(s)")
        try:
            revision_row = connection.execute("SELECT version_num FROM alembic_version").fetchone()
        except sqlite3.Error as exc:
            raise BackfillError(f"Database revision table could not be read: {exc}") from exc
        revision = revision_row[0] if revision_row else None
        if revision != expected_revision:
            raise BackfillError(f"Database revision is {revision!r}; expected {expected_revision}")
        validated = {entry.document_id: _validate_manifest_entry(connection, entry) for entry in FIXED_MANIFEST}
        return revision, validated
    finally:
        connection.close()


def _candidate_groups(documents: Iterable[ValidatedDocument]) -> dict[tuple[str, str, str], list[dict]]:
    groups: dict[tuple[str, str, str], list[dict]] = {}
    for document in documents:
        for mention in document.mentions:
            key = (document.entry.investigation_id, mention["evidence_type"], mention["normalized_value"])
            groups.setdefault(key, []).append(mention)
    return {key: values for key, values in groups.items() if len({item["document_id"] for item in values}) >= 2}


def _database_deltas(path: Path, documents: Iterable[ValidatedDocument]) -> dict[str, int]:
    connection = _readonly_connection(path)
    inserted = refreshed = skipped = 0
    candidates_created = candidates_refreshed = 0
    try:
        for document in documents:
            existing = connection.execute(
                """SELECT source_sha256,block_id,evidence_type,normalized_value,start_offset,end_offset,
                          matcher_version,is_current
                   FROM deterministic_evidence_mentions WHERE document_id=?""",
                (document.entry.document_id,),
            ).fetchall()
            by_identity = {
                (r["source_sha256"],r["block_id"],r["evidence_type"],r["normalized_value"],r["start_offset"],r["end_offset"],r["matcher_version"]): bool(r["is_current"])
                for r in existing
            }
            for mention in document.mentions:
                key = (document.entry.source_sha256, mention["block_id"], mention["evidence_type"], mention["normalized_value"], mention["start_offset"], mention["end_offset"], EXPECTED_MATCHER_VERSION)
                if key not in by_identity:
                    inserted += 1
                elif by_identity[key]:
                    skipped += 1
                else:
                    refreshed += 1
        for (investigation_id, evidence_type, value), _ in _candidate_groups(documents).items():
            key = _candidate_key(investigation_id, evidence_type, value)
            found = connection.execute(
                "SELECT 1 FROM relationship_candidates WHERE investigation_id=? AND candidate_key=?",
                (investigation_id, key),
            ).fetchone()
            if found:
                candidates_refreshed += 1
            else:
                candidates_created += 1
    finally:
        connection.close()
    return {
        "mentions_inserted": inserted,
        "mentions_refreshed": refreshed,
        "mentions_skipped": skipped,
        "candidate_groups_created": candidates_created,
        "candidate_groups_refreshed": candidates_refreshed,
        "candidate_groups_skipped": 0,
    }


def _mention_identity(row: dict) -> tuple:
    return (
        str(row["document_id"]), str(row["source_sha256"]), str(row["block_id"]),
        str(row["evidence_type"]), str(row["normalized_value"]), int(row["start_offset"]),
        int(row["end_offset"]), str(row["matcher_version"]),
    )


def _canonical_uuid(value: object) -> str:
    try:
        return str(uuid.UUID(str(value)))
    except (AttributeError, TypeError, ValueError):
        return str(value)


def _reference_from_row(row: dict) -> dict:
    return {
        "investigation_id": _canonical_uuid(row["investigation_id"]),
        "document_id": _canonical_uuid(row["document_id"]),
        "filename": row["filename"],
        "source_sha256": row["source_sha256"],
        "field_type": row["evidence_type"],
        "raw_value": row["original_text"],
        "normalized_value": row["normalized_value"],
        "block_id": row["block_id"],
        "page_number": row["page_number"],
        "extraction_method": row["extraction_method"],
        "label_context": row["label_context"],
        "start_offset": row["start_offset"],
        "end_offset": row["end_offset"],
        "bounding_box": json.loads(row["coordinates"]) if isinstance(row["coordinates"],str) else row["coordinates"],
        "evidence_strength": row["strength"],
    }


def _reconcile_document(path: Path, document: ValidatedDocument) -> bool:
    """Return true only when the committed mention and candidate state is complete."""
    connection = _readonly_connection(path)
    entry = document.entry
    try:
        stored_rows = [dict(row) for row in connection.execute(
            """SELECT investigation_id,document_id,filename,source_sha256,evidence_type,original_text,
                      normalized_value,block_id,page_number,extraction_method,label_context,start_offset,
                      end_offset,coordinates,strength,matcher_version,is_current
               FROM deterministic_evidence_mentions WHERE document_id=? AND is_current=1""",
            (entry.document_id,),
        )]
        expected = {_mention_identity(row) for row in document.mentions}
        actual = {_mention_identity(row) for row in stored_rows}
        if len(actual) != len(stored_rows) or actual != expected:
            return False
        for row in stored_rows:
            mention = next((m for m in document.mentions if _mention_identity(m) == _mention_identity(row)), None)
            if mention is None:
                return False
            coordinates = json.loads(row["coordinates"]) if isinstance(row["coordinates"],str) else row["coordinates"]
            if any((row[k] or None) != (mention.get(mk) or None) for k, mk in (
                ("filename", "filename"), ("original_text", "original_text"),
                ("page_number", "page_number"), ("extraction_method", "extraction_method"),
                ("label_context", "label_context"), ("end_offset", "end_offset"),
                ("strength", "strength"),
            )) or row["start_offset"] != mention["start_offset"] or coordinates != mention.get("coordinates"):
                return False

        affected = {
            (str(row["evidence_type"]), str(row["normalized_value"]))
            for row in connection.execute(
                "SELECT DISTINCT evidence_type,normalized_value FROM deterministic_evidence_mentions WHERE document_id=?",
                (entry.document_id,),
            )
        }
        affected |= {(m["evidence_type"],m["normalized_value"]) for m in document.mentions}
        for evidence_type, normalized_value in affected:
            current_rows = [dict(row) for row in connection.execute(
                """SELECT investigation_id,document_id,filename,source_sha256,evidence_type,original_text,
                          normalized_value,block_id,page_number,extraction_method,label_context,start_offset,
                          end_offset,coordinates,strength
                   FROM deterministic_evidence_mentions
                   WHERE investigation_id=? AND evidence_type=? AND normalized_value=? AND is_current=1
                   ORDER BY document_id,block_id,start_offset""",
                (entry.investigation_id,evidence_type,normalized_value),
            )]
            docs = sorted({_canonical_uuid(row["document_id"]) for row in current_rows})
            candidate_key = _candidate_key(entry.investigation_id,evidence_type,normalized_value)
            candidate = connection.execute(
                "SELECT lifecycle_status,document_ids,evidence_refs FROM relationship_candidates WHERE investigation_id=? AND candidate_key=?",
                (entry.investigation_id,candidate_key),
            ).fetchone()
            if candidate is None:
                if len(docs) >= 2:
                    return False
                continue
            expected_refs = [_reference_from_row(row) for row in current_rows]
            actual_docs = json.loads(candidate["document_ids"]) if isinstance(candidate["document_ids"],str) else candidate["document_ids"]
            actual_refs = json.loads(candidate["evidence_refs"]) if isinstance(candidate["evidence_refs"],str) else candidate["evidence_refs"]
            lifecycle = "active" if len(docs) > 1 else "stale"
            if sorted(_canonical_uuid(value) for value in (actual_docs or [])) != docs or actual_refs != expected_refs or candidate["lifecycle_status"] != lifecycle:
                return False
    finally:
        connection.close()
    return True


def _ensure_ledger(path: Path, run_id: str, database_path: Path, revision: str, manifest_hash: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path, timeout=30)
    try:
        connection.execute("PRAGMA journal_mode=WAL")
        connection.executescript("""
            CREATE TABLE IF NOT EXISTS backfill_runs (
                run_id TEXT PRIMARY KEY, manifest_hash TEXT NOT NULL, matcher_version TEXT NOT NULL,
                database_path TEXT NOT NULL, alembic_revision TEXT NOT NULL, status TEXT NOT NULL,
                started_at TEXT NOT NULL, finished_at TEXT, report_json TEXT
            );
            CREATE TABLE IF NOT EXISTS backfill_documents (
                run_id TEXT NOT NULL REFERENCES backfill_runs(run_id) ON DELETE CASCADE,
                document_id TEXT NOT NULL, status TEXT NOT NULL, attempts INTEGER NOT NULL DEFAULT 0,
                error TEXT, source_blocks_validated INTEGER NOT NULL DEFAULT 0,
                mentions_inserted INTEGER NOT NULL DEFAULT 0, mentions_refreshed INTEGER NOT NULL DEFAULT 0,
                mentions_skipped INTEGER NOT NULL DEFAULT 0, candidate_groups_created INTEGER NOT NULL DEFAULT 0,
                candidate_groups_refreshed INTEGER NOT NULL DEFAULT 0, candidate_groups_skipped INTEGER NOT NULL DEFAULT 0,
                retry_count INTEGER NOT NULL DEFAULT 0, reconciliation_count INTEGER NOT NULL DEFAULT 0,
                duration_seconds REAL NOT NULL DEFAULT 0, updated_at TEXT NOT NULL,
                PRIMARY KEY(run_id,document_id)
            );
        """)
        connection.execute("BEGIN IMMEDIATE")
        existing = connection.execute(
            "SELECT manifest_hash,matcher_version,database_path,alembic_revision FROM backfill_runs WHERE run_id=?",
            (run_id,),
        ).fetchone()
        identity = (manifest_hash, EXPECTED_MATCHER_VERSION, str(database_path), revision)
        if existing is None:
            connection.execute(
                "INSERT INTO backfill_runs(run_id,manifest_hash,matcher_version,database_path,alembic_revision,status,started_at) VALUES(?,?,?,?,?,?,?)",
                (run_id,*identity,"running",_now()),
            )
            connection.executemany(
                "INSERT INTO backfill_documents(run_id,document_id,status,updated_at) VALUES(?,?,?,?)",
                [(run_id,e.document_id,"Pending",_now()) for e in FIXED_MANIFEST],
            )
        elif tuple(existing) != identity:
            raise BackfillError("Run ID already exists with a different manifest or database identity")
        connection.execute("UPDATE backfill_runs SET status='running',finished_at=NULL WHERE run_id=?",(run_id,))
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _ledger_doc(path: Path, run_id: str, document_id: str) -> sqlite3.Row:
    connection = sqlite3.connect(path)
    connection.row_factory = sqlite3.Row
    try:
        row = connection.execute(
            "SELECT * FROM backfill_documents WHERE run_id=? AND document_id=?",(run_id,document_id),
        ).fetchone()
        if row is None:
            raise BackfillError(f"Missing per-document ledger row: {document_id}")
        return row
    finally:
        connection.close()


def _update_ledger_doc(path: Path, run_id: str, document_id: str, **values) -> None:
    allowed = {
        "status","attempts","error","source_blocks_validated","mentions_inserted","mentions_refreshed",
        "mentions_skipped","candidate_groups_created","candidate_groups_refreshed","candidate_groups_skipped",
        "retry_count","reconciliation_count","duration_seconds",
    }
    if not values or not set(values) <= allowed:
        raise ValueError("Invalid ledger fields")
    assignments = ",".join(f"{key}=?" for key in values)
    connection = sqlite3.connect(path,timeout=30)
    try:
        connection.execute("BEGIN IMMEDIATE")
        connection.execute(
            f"UPDATE backfill_documents SET {assignments},updated_at=? WHERE run_id=? AND document_id=?",
            (*values.values(),_now(),run_id,document_id),
        )
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def _load_run_docs(path: Path, run_id: str) -> list[sqlite3.Row]:
    connection = sqlite3.connect(path)
    connection.row_factory = sqlite3.Row
    try:
        return connection.execute(
            "SELECT * FROM backfill_documents WHERE run_id=? ORDER BY rowid",(run_id,),
        ).fetchall()
    finally:
        connection.close()


def _finish_run(path: Path, run_id: str, status: str, report: dict) -> None:
    connection = sqlite3.connect(path,timeout=30)
    try:
        connection.execute(
            "UPDATE backfill_runs SET status=?,finished_at=?,report_json=? WHERE run_id=?",
            (status,_now(),json.dumps(report,sort_keys=True),run_id),
        )
        connection.commit()
    finally:
        connection.close()


@contextmanager
def _exclusive_lock(state_database: Path):
    lock_path = state_database.with_suffix(state_database.suffix + ".lock")
    lock_path.parent.mkdir(parents=True,exist_ok=True)
    handle = lock_path.open("a+b")
    try:
        if os.name == "nt":
            import msvcrt
            if os.fstat(handle.fileno()).st_size == 0:
                handle.seek(0)
                handle.write(b"0")
                handle.flush()
            handle.seek(0)
            try:
                msvcrt.locking(handle.fileno(),msvcrt.LK_NBLCK,1)
            except OSError as exc:
                raise ConcurrentBackfillError("Another relationship backfill runner holds the lock") from exc
        else:
            import fcntl
            try:
                fcntl.flock(handle.fileno(),fcntl.LOCK_EX|fcntl.LOCK_NB)
            except OSError as exc:
                raise ConcurrentBackfillError("Another relationship backfill runner holds the lock") from exc
        yield
    finally:
        try:
            if os.name == "nt":
                import msvcrt
                handle.seek(0)
                msvcrt.locking(handle.fileno(),msvcrt.LK_UNLCK,1)
            else:
                import fcntl
                fcntl.flock(handle.fileno(),fcntl.LOCK_UN)
        except OSError:
            pass
        handle.close()


async def _persist_one(database_path: Path, document: ValidatedDocument) -> dict[str, int]:
    url = f"sqlite+aiosqlite:///{database_path.as_posix()}"
    engine = create_async_engine(url,connect_args={"timeout":30})

    @event.listens_for(engine.sync_engine,"connect")
    def _enable_foreign_keys(dbapi_connection, _connection_record):
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    sessions = async_sessionmaker(engine,expire_on_commit=False)
    entry = document.entry
    mention_keys = {
        (m["source_sha256"],m["block_id"],m["evidence_type"],m["normalized_value"],m["start_offset"],m["end_offset"],m["matcher_version"])
        for m in document.mentions
    }
    try:
        async with sessions() as session:
            async with session.begin():
                current_document = await session.get(Document,uuid.UUID(entry.document_id))
                current_ocr = await session.scalar(
                    select(OCRResult).where(OCRResult.document_id==uuid.UUID(entry.document_id))
                )
                current_spans = (await session.scalars(
                    select(EvidenceSpan).where(EvidenceSpan.document_id==uuid.UUID(entry.document_id))
                )).all()
                if current_document is None or (
                    current_document.investigation_id.hex!=entry.investigation_id
                    or current_document.filename!=entry.filename
                    or current_document.sha256!=entry.source_sha256
                ):
                    raise BackfillError(f"Document identity or stored hash changed at transaction start: {entry.document_id}")
                if current_ocr is None or current_ocr.id!=uuid.UUID(entry.ocr_result_id):
                    raise BackfillError(f"OCR result identity changed at transaction start: {entry.document_id}")
                current_blocks=current_ocr.blocks if isinstance(current_ocr.blocks,list) else None
                if current_blocks is None or current_blocks!=document.blocks:
                    raise BackfillError(f"Stored OCR blocks changed at transaction start: {entry.document_id}")
                _validate_block_span_provenance(entry,current_blocks,[{
                    "block_id":span.block_id,"text":span.text,"extraction_method":span.extraction_method,
                    "source_sha256":span.source_sha256,
                } for span in current_spans])
                existing_mentions = (await session.scalars(
                    select(DeterministicEvidenceMention).where(DeterministicEvidenceMention.document_id==uuid.UUID(entry.document_id))
                )).all()
                existing_by_key = {
                    (r.source_sha256,r.block_id,r.evidence_type,r.normalized_value,r.start_offset,r.end_offset,r.matcher_version):r
                    for r in existing_mentions
                }
                candidate_keys = {
                    (entry.investigation_id, r.evidence_type, r.normalized_value)
                    for r in existing_mentions if r.is_current
                }
                candidate_keys |= {
                    (entry.investigation_id, m["evidence_type"], m["normalized_value"])
                    for m in document.mentions
                }
                inserted = sum(1 for key in mention_keys if key not in existing_by_key)
                refreshed = sum(1 for key in mention_keys if key in existing_by_key)
                skipped = 0
                existing_candidates = (await session.scalars(
                    select(RelationshipCandidate).where(
                        RelationshipCandidate.investigation_id==uuid.UUID(entry.investigation_id),
                        RelationshipCandidate.candidate_key.in_([
                            _candidate_key(i,t,v) for i,t,v in candidate_keys
                        ] or ["__none__"]),
                    )
                )).all()
                existing_candidate_keys = {row.candidate_key for row in existing_candidates}
                result = await persist_document_mentions_and_candidates(
                    session,
                    investigation_id=entry.investigation_id,
                    document_id=entry.document_id,
                    filename=entry.filename,
                    source_sha256=entry.source_sha256,
                    blocks=document.blocks,
                )
                candidate_created = sum(1 for row in result["candidates"] if row.candidate_key not in existing_candidate_keys)
                candidate_refreshed = sum(1 for row in result["candidates"] if row.candidate_key in existing_candidate_keys)
                await session.flush()
            # Leaving session.begin commits mentions and candidates atomically.
        return {
            "mentions_inserted": inserted,
            "mentions_refreshed": refreshed,
            "mentions_skipped": skipped,
            "candidate_groups_created": candidate_created,
            "candidate_groups_refreshed": candidate_refreshed,
            "candidate_groups_skipped": 0,
        }
    finally:
        await engine.dispose()


def _integrity(path: Path) -> tuple[str, int]:
    connection = _readonly_connection(path)
    try:
        rows = [tuple(row) for row in connection.execute("PRAGMA integrity_check").fetchall()]
        fk = connection.execute("PRAGMA foreign_key_check").fetchall()
        return ("ok" if rows == [("ok",)] else repr(rows),len(fk))
    finally:
        connection.close()


def run_backfill(
    *,
    database_path: Path | str = CANONICAL_DATABASE,
    execute: bool = False,
    run_id: str | None = None,
    state_database: Path | str = DEFAULT_STATE_DATABASE,
    expected_database_path: Path | str = CANONICAL_DATABASE,
    expected_revision: str = EXPECTED_REVISION,
    expected_matcher_version: str = EXPECTED_MATCHER_VERSION,
    _after_document_commit: Callable[[str], None] | None = None,
    _before_document: Callable[[str], None] | None = None,
) -> dict:
    """Validate and dry-run by default; persist only when execute=True.

    The private hooks are available for isolated crash/failure tests only.
    The command-line interface does not expose a database path override.
    """
    started = time.perf_counter()
    path = _resolve_expected_path(database_path,expected_database_path)
    state_path = Path(state_database).absolute()
    if state_path == path:
        raise BackfillError("Run ledger must be separate from the canonical database")
    try:
        revision, documents = _validate_database(path,expected_revision,expected_matcher_version)
    except sqlite3.Error as exc:
        raise BackfillError(f"Database validation failed: {exc}") from exc
    documents_in_order = [documents[entry.document_id] for entry in FIXED_MANIFEST]
    all_mentions = [mention for doc in documents_in_order for mention in doc.mentions]
    groups = _candidate_groups(documents_in_order)
    if len(all_mentions) != EXPECTED_MENTION_COUNT or len(groups) != EXPECTED_CANDIDATE_GROUP_COUNT:
        raise BackfillError(
            f"Pinned preflight totals changed: mentions={len(all_mentions)}, candidates={len(groups)}"
        )
    try:
        run_id = str(uuid.UUID(run_id)) if run_id else str(uuid.uuid4())
    except (AttributeError, TypeError, ValueError) as exc:
        raise BackfillError("Run ID must be a UUID") from exc
    manifest_hash = _manifest_hash()
    state = {
        "mentions_inserted": 0,"mentions_refreshed": 0,"mentions_skipped": 0,
        "candidate_groups_created": 0,"candidate_groups_refreshed": 0,"candidate_groups_skipped": 0,
    }
    if not execute:
        state = _database_deltas(path,documents_in_order)
        integrity,fk_count = _integrity(path)
        return {
            "run_id":run_id,"manifest_hash":manifest_hash,"database_path":str(path),
            "alembic_revision":revision,"matcher_version":EXPECTED_MATCHER_VERSION,
            "status":"dry_run","documents":{"Pending":len(FIXED_MANIFEST),"Processing":0,"Completed":0,"Failed":0},
            "source_blocks_validated":sum(doc.entry.source_block_count for doc in documents_in_order),
            **state,"retry_count":0,"reconciliation_count":0,
            "document_durations_seconds":{
                doc.entry.document_id:doc.validation_seconds for doc in documents_in_order
            },"duration_seconds":round(time.perf_counter()-started,4),
            "integrity_check":integrity,"foreign_key_violations":fk_count,
        }

    with _exclusive_lock(state_path):
        _ensure_ledger(state_path,run_id,path,revision,manifest_hash)
        run_started = time.perf_counter()
        for entry in FIXED_MANIFEST:
            doc = documents[entry.document_id]
            ledger = _ledger_doc(state_path,run_id,entry.document_id)
            old_status = ledger["status"]
            if old_status == "Completed":
                if not _reconcile_document(path,doc):
                    _update_ledger_doc(state_path,run_id,entry.document_id,status="Failed",error="Completed state no longer matches persisted occurrence/candidate state")
                    old_status = "Failed"
                else:
                    _update_ledger_doc(
                        state_path,run_id,entry.document_id,
                        mentions_inserted=0,mentions_refreshed=0,
                        mentions_skipped=len(doc.mentions),
                        candidate_groups_created=0,candidate_groups_refreshed=0,
                        candidate_groups_skipped=sum(1 for refs in groups.values() if any(m["document_id"]==entry.document_id for m in refs)),
                    )
                    continue
            attempts = int(ledger["attempts"])
            retry_count = int(ledger["retry_count"])
            reconciliation_count = int(ledger["reconciliation_count"])
            if old_status in {"Processing","Failed"}:
                retry_count += 1
                if _reconcile_document(path,doc):
                    reconciliation_count += 1
                    _update_ledger_doc(
                        state_path,run_id,entry.document_id,status="Completed",error=None,
                        source_blocks_validated=entry.source_block_count,
                        mentions_inserted=0,mentions_refreshed=0,
                        mentions_skipped=len(doc.mentions),
                        candidate_groups_created=0,candidate_groups_refreshed=0,
                        candidate_groups_skipped=sum(1 for refs in groups.values() if any(m["document_id"]==entry.document_id for m in refs)),
                        retry_count=retry_count,reconciliation_count=reconciliation_count,
                    )
                    continue
            doc_started = time.perf_counter()
            try:
                if _before_document:
                    _before_document(entry.document_id)
                # Recheck this entry immediately before touching it. The full
                # manifest was validated before the run record was created.
                check_connection = _readonly_connection(path)
                try:
                    doc = _validate_manifest_entry(check_connection,entry)
                finally:
                    check_connection.close()
                _update_ledger_doc(
                    state_path,run_id,entry.document_id,status="Processing",error=None,
                    attempts=attempts+1,retry_count=retry_count,
                )
                delta = asyncio.run(_persist_one(path,doc))
                if _after_document_commit:
                    _after_document_commit(entry.document_id)
                if not _reconcile_document(path,doc):
                    raise BackfillError("Committed state failed reconciliation")
                duration = round(time.perf_counter()-doc_started,4)
                _update_ledger_doc(
                    state_path,run_id,entry.document_id,status="Completed",error=None,
                    source_blocks_validated=entry.source_block_count,
                    duration_seconds=duration,
                    retry_count=retry_count,reconciliation_count=reconciliation_count,
                    **delta,
                )
            except Exception as exc:
                duration = round(time.perf_counter()-doc_started,4)
                _update_ledger_doc(
                    state_path,run_id,entry.document_id,status="Failed",error=f"{type(exc).__name__}: {exc}"[:1000],
                    duration_seconds=duration,retry_count=retry_count,
                    attempts=max(attempts+1,int(_ledger_doc(state_path,run_id,entry.document_id)["attempts"])),
                )

        ledger_rows = _load_run_docs(state_path,run_id)
        statuses = {name:sum(1 for row in ledger_rows if row["status"]==name) for name in ("Pending","Processing","Completed","Failed")}
        for row in ledger_rows:
            for key in state:
                state[key] += int(row[key])
        retries = sum(int(row["retry_count"]) for row in ledger_rows)
        reconciliations = sum(int(row["reconciliation_count"]) for row in ledger_rows)
        durations = {row["document_id"]:round(float(row["duration_seconds"]),4) for row in ledger_rows if row["duration_seconds"]}
        integrity,fk_count = _integrity(path)
        success = statuses["Pending"]==0 and statuses["Processing"]==0 and statuses["Failed"]==0 and integrity=="ok" and fk_count==0
        report = {
            "run_id":run_id,"manifest_hash":manifest_hash,"database_path":str(path),
            "alembic_revision":revision,"matcher_version":EXPECTED_MATCHER_VERSION,
            "status":"completed" if success else "failed","documents":statuses,
            "document_errors":{
                row["document_id"]:row["error"] for row in ledger_rows if row["error"]
            },
            "source_blocks_validated":sum(int(row["source_blocks_validated"]) for row in ledger_rows),
            **state,"retry_count":retries,"reconciliation_count":reconciliations,
            "document_durations_seconds":durations,
            "duration_seconds":round(time.perf_counter()-run_started,4),
            "integrity_check":integrity,"foreign_key_violations":fk_count,
        }
        _finish_run(state_path,run_id,report["status"],report)
        return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Fixed-manifest historical relationship-index backfill")
    parser.add_argument("--execute",action="store_true",help="persist the backfill; omission is read-only dry-run")
    parser.add_argument("--run-id",help="resume an existing execution run ID")
    args = parser.parse_args(argv)
    try:
        report = run_backfill(execute=args.execute,run_id=args.run_id)
    except BackfillError as exc:
        print(json.dumps({"status":"failed","error":str(exc)},indent=2))
        return 2
    print(json.dumps(report,indent=2,sort_keys=True))
    return 0 if report["status"] in {"dry_run","completed"} else 1


if __name__ == "__main__":
    raise SystemExit(main())

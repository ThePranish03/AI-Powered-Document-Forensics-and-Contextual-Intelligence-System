"""Read-only regression checks for the three supplied source PDFs.

Set DOCFORENSIQ_SOURCE_FIXTURE_DIR to the directory containing the source PDFs.
The documents stay outside the repository and are only read by this test.
"""
import os
import json
import re
from pathlib import Path
from dataclasses import replace

import pytest

from app.services.ocr_metrics import extract_document_facts
from app.services.ocr_worker import _native_pdf_blocks
from app.core.config import settings
from app.services import relationship_extraction


FIXTURE_DIR = os.getenv("DOCFORENSIQ_SOURCE_FIXTURE_DIR")
pytestmark = pytest.mark.skipif(not FIXTURE_DIR, reason="source PDF fixture directory is not configured")


@pytest.mark.parametrize(("filename", "expected_rows"), [
    ("doc1_corporate_registry_ownership.pdf", "ownership_rows"),
    ("doc2_financial_wire_ledger.pdf", "transaction_rows"),
    ("doc3_communications_access_logs.pdf", "access_session_rows"),
])
def test_supplied_pdf_native_blocks_and_structured_rows(filename, expected_rows, tmp_path):
    source = Path(FIXTURE_DIR) / filename
    assert source.is_file(), f"Required supplied source is missing: {filename}"
    extensionless = tmp_path / "secure-upload-key"
    extensionless.write_bytes(source.read_bytes())

    blocks = _native_pdf_blocks(extensionless)
    facts = extract_document_facts({"document_id": filename, "filename": filename, "blocks": blocks})

    assert len(blocks) >= 8
    assert {block["page_index"] for block in blocks} == {0, 1}
    assert all(block["extraction_method"].startswith("native_pdf") for block in blocks)
    assert len(facts[expected_rows]) == 3 if expected_rows == "access_session_rows" else len(facts[expected_rows]) == 4
    assert facts["structured_fields"]
    assert all(row.get("block_id") and row.get("page_index") in {0, 1} for row in facts[expected_rows])


@pytest.mark.parametrize("filename", [
    "doc1_corporate_registry_ownership.pdf",
    "doc2_financial_wire_ledger.pdf",
    "doc3_communications_access_logs.pdf",
])
def test_supplied_pdf_blocks_pass_relationship_grounding_pipeline(filename, monkeypatch):
    source = Path(FIXTURE_DIR) / filename
    assert source.is_file(), f"Required supplied source is missing: {filename}"
    blocks = _native_pdf_blocks(source)
    monkeypatch.setattr(relationship_extraction, "settings", replace(
        settings, llm_api_key="synthetic-regression-token",
        llm_relationship_chunk_characters=4500, llm_relationship_chunk_overlap=400,
    ))

    def grounded_model(messages, **_kwargs):
        supplied = json.loads(messages[1]["content"])
        source_block = next(item for item in supplied if item["text"].strip())
        words = list(dict.fromkeys(re.findall(r"[A-Za-z0-9][A-Za-z0-9'.-]*", source_block["text"])))
        first = words[0]
        second = next((word for word in words[1:] if word.casefold() != first.casefold()), first)
        entities = [
            {"mention_id": "e1", "entity_type": "OTHER", "mention_text": first, "source_block_ids": [source_block["block_id"]], "confidence": None},
            {"mention_id": "e2", "entity_type": "OTHER", "mention_text": second, "source_block_ids": [source_block["block_id"]], "confidence": None},
        ]
        relationships = [] if second == first else [{
            "relationship_id": "r1", "subject_mention_id": "e1", "predicate": "ASSOCIATED_WITH",
            "object_mention_id": "e2", "relationship_kind": "explicit",
            "source_block_ids": [source_block["block_id"]], "confidence": None,
        }]
        return {"content": json.dumps({"schema_version": "relationship_extraction_v1", "entities": entities, "relationships": relationships})}

    monkeypatch.setattr(relationship_extraction, "chat_completion", grounded_model)
    graph, metadata = relationship_extraction.extract(blocks)

    input_ids = {block["block_id"] for block in blocks}
    assert graph["entities"]
    assert metadata["chunk_count"] >= 1
    assert all(set(entity["source_block_ids"]) <= input_ids for entity in graph["entities"])
    assert all(set(relation["source_block_ids"]) <= input_ids for relation in graph["relationships"])
    assert graph["relationships"]

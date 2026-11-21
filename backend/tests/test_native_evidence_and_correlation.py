import fitz
from reportlab.lib import colors
from reportlab.lib.pagesizes import letter
from reportlab.platypus import PageBreak, SimpleDocTemplate, Table, TableStyle

from app.services.ocr_metrics import analyze_documents, build_cross_document_correlation, extract_document_facts
from app.services.ocr_worker import _native_pdf_blocks


def test_native_pdf_blocks_keep_method_page_bbox_and_no_fabricated_confidence(tmp_path):
    path = tmp_path / "native.pdf"
    pdf = fitz.open()
    page = pdf.new_page(width=300, height=200)
    page.insert_text((24, 48), "Document ID: DOC-REL-001 | Case Link: INV-2026-0894-B")
    page.insert_text((24, 90), "TXN-90411 $420,000.00")
    pdf.save(path)
    pdf.close()

    blocks = _native_pdf_blocks(path)
    assert blocks
    assert all(block["extraction_method"] == "native_pdf_text" for block in blocks)
    assert all(block["page_index"] == 0 for block in blocks)
    assert all(block["bbox_space"] == "pdf_points" for block in blocks)
    assert all(block["engine_confidence"] is None for block in blocks)
    assert any("TXN-90411" in block["text"] for block in blocks)


def test_native_pdf_table_rows_keep_cells_and_feed_transaction_access_rules(tmp_path):
    path = tmp_path / "tables.pdf"
    styles = TableStyle([
        ("GRID", (0, 0), (-1, -1), 0.5, colors.black),
        ("BACKGROUND", (0, 0), (-1, 0), colors.lightgrey),
        ("FONTSIZE", (0, 0), (-1, -1), 7),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
    ])
    owner_table = Table([
        ["Full Name", "Role / Title", "Identifier", "Ownership %", "Known Alias / Contact"],
        ["Marcus Vance", "Managing Director & UBO", "EMP-4092", "65.0%", "m.vance@example.test"],
    ], repeatRows=1)
    ledger_table = Table([
        ["Tx ID", "Date (UTC)", "Originator", "Beneficiary Entity", "Beneficiary Account", "Amount (USD)", "Approver"],
        *[[f"TXN-904{11 + index}", f"2026-05-{11 + index}", "NexaCore", "Aether Freight", "CHASE-US-7741902", "$420,000.00", "Elena Rostova"] for index in range(4)],
    ], repeatRows=1)
    access_table = Table([
        ["Session ID", "Timestamp (UTC)", "User Account", "Source IP", "Target Resource Accessed"],
        ["SES-7710", "2026-05-11 09:10:04", "elena@example.test", "185.220.101.14", "ERP Payment Portal"],
        ["SES-7711", "2026-05-11 09:12:49", "marcus@example.test", "185.220.101.14", "Vendor Master File"],
    ], repeatRows=1)
    for table in (owner_table, ledger_table, access_table):
        table.setStyle(styles)
    story = [owner_table, PageBreak(), ledger_table, PageBreak(), access_table]
    SimpleDocTemplate(str(path), pagesize=letter).build(story)

    blocks = _native_pdf_blocks(path)
    facts = extract_document_facts({"document_id": "table-doc", "filename": path.name, "blocks": blocks})
    result = analyze_documents([{"document_id": "table-doc", "filename": path.name, "blocks": blocks}])

    assert len(facts["ownership_rows"]) == 1
    assert facts["ownership_rows"][0]["fields"]["ownership %"]["raw"] == "65.0%"
    assert len(facts["transaction_rows"]) == 4
    assert {row["fields"]["tx id"]["normalized"] for row in facts["transaction_rows"]} == {"TXN-90411", "TXN-90412", "TXN-90413", "TXN-90414"}
    assert len(facts["access_session_rows"]) == 2
    assert any(item["finding_type"] == "shared_access_ip" for item in result["findings"])


def test_document_id_and_case_link_are_distinct_typed_fields():
    facts = extract_document_facts({
        "document_id": "doc-1",
        "blocks": [{
            "block_id": "b1",
            "page_index": 0,
            "text": "Document ID: DOC-REL-001 | Case Link: INV-2026-0894-B",
            "extraction_method": "native_pdf_text",
        }],
    })

    assert {(item["field_type"], item["normalized"]) for item in facts["structured_fields"]} == {
        ("document_id", "DOC-REL-001"),
        ("case_link", "INV-2026-0894-B"),
    }
    assert {(item["entity_type"], item["value"]) for item in facts["identifiers"]} == {
        ("DOCUMENT_ID", "DOC-REL-001"),
        ("CASE_LINK", "INV-2026-0894-B"),
    }


def test_case_link_and_grounded_relationships_become_cited_review_candidates():
    documents = []
    for index in range(2):
        document_id = f"doc-{index}"
        block_id = f"b-{index}"
        text = "Case Link: INV-2026-0894-B Aether Freight Systems Inc."
        documents.append({
            "document_id": document_id,
            "filename": f"source-{index}.pdf",
            "sha256": str(index) * 64,
            "blocks": [{"block_id": block_id, "page_index": index, "text": text, "extraction_method": "native_pdf_text"}],
            "structured_extraction": {
                "entities": [{
                    "mention_id": f"m-{index}",
                    "entity_type": "ORGANIZATION",
                    "mention_text": "Aether Freight Systems Inc.",
                    "source_block_ids": [block_id],
                    "canonical_entity_id": "entity-aether",
                }],
                "relationships": [{
                    "relationship_id": f"r-{index}",
                    "subject_mention_id": f"m-{index}",
                    "predicate": "ASSOCIATED_WITH",
                    "object_mention_id": f"m-{index}",
                    "relationship_kind": "explicit",
                    "source_block_ids": [block_id],
                }],
            },
        })

    result = build_cross_document_correlation(documents)
    case_link = next(item for item in result["links"] if item["relation_type"] == "shared_case_link")
    entity_link = next(item for item in result["links"] if item["relation_type"] == "shared_canonical_entity")

    assert case_link["review_status"] == "candidate"
    assert {ref["document_id"] for ref in case_link["evidence_refs"]} == {"doc-0", "doc-1"}
    assert entity_link["review_status"] == "candidate"
    assert {ref["block_id"] for ref in entity_link["evidence_refs"]} == {"b-0", "b-1"}
    assert all(ref["relationship_kind"] == "grounded_mention" for ref in entity_link["evidence_refs"])


def test_findings_diagnostics_distinguish_missing_input_and_extraction_error():
    result = analyze_documents([{
        "document_id": "failed-doc",
        "filename": "failed.pdf",
        "processing_status": "failed",
        "blocks": [],
        "text": "",
    }])
    rules = {item["rule_id"]: item for item in result["diagnostics"]["rules"]}

    assert rules["subtotal_plus_tax_vs_total"]["rules_skipped"][0]["status"] == "error"
    assert rules["date_extraction"]["rules_skipped"][0]["status"] == "extraction_incomplete"
    assert rules["low_confidence_regions"]["rules_skipped"][0]["status"] == "missing_input"

    relationship_only_failure = analyze_documents([{
        "document_id": "relationship-failed-doc",
        "filename": "relationship-failed.pdf",
        "processing_status": "relationship_extraction_failed",
        "text": "Source document contains readable evidence.",
        "blocks": [{"block_id": "native-1", "page_index": 0, "text": "Source document contains readable evidence.", "extraction_method": "native_pdf_text"}],
    }])
    relationship_rules = {item["rule_id"]: item for item in relationship_only_failure["diagnostics"]["rules"]}
    assert relationship_rules["subtotal_plus_tax_vs_total"]["rules_skipped"][0]["status"] == "not_applicable"

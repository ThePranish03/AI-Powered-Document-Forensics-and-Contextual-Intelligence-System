from app.services.deterministic_relationships import build_deterministic_relationship_candidates
from app.services.ocr_metrics import build_cross_document_correlation, extract_document_facts


def _facts(document_id, text):
    block = {
        "block_id": f"block-{document_id}", "page_index": 1,
        "extraction_method": "ocr", "text": text,
    }
    return extract_document_facts({
        "document_id": document_id, "filename": f"{document_id}.pdf",
        "sha256": document_id * 64, "blocks": [block],
    })


def test_exact_validated_identifiers_and_labeled_names_create_cited_candidates():
    facts = [
        _facts("doc-a", "Company: Northstar Logistics Ltd.\nFull Name: Elena Rostova\nEmail: elena@example.test\nPhone: +1 (415) 555-0134\nSource IP: 192.0.2.18\nRegistration / Tax ID: SG-2048120K"),
        _facts("doc-b", "Company: Northstar Logistics Ltd.\nFull Name: Elena Rostova\nEmail: ELENA@EXAMPLE.TEST\nPhone: +1-415-555-0134\nSource IP: 192.0.2.18\nRegistration / Tax ID: SG-2048120K"),
    ]

    links = build_deterministic_relationship_candidates(facts)
    by_type = {item["identifier_type"]: item for item in links}

    assert {"EMAIL", "PHONE", "IP", "REGISTRATION_OR_TAX_ID", "ORGANIZATION", "PERSON"} <= set(by_type)
    for link in links:
        assert link["review_status"] == "candidate"
        assert len(link["document_ids"]) == 2
        assert {ref["document_id"] for ref in link["evidence_refs"]} == {"doc-a", "doc-b"}
        assert all(ref["block_id"] and ref["page_number"] == 2 and ref["quoted_text"] for ref in link["evidence_refs"])


def test_exact_link_rules_do_not_use_common_names_dates_or_amounts_as_identity():
    facts = [
        _facts("doc-a", "Name: John\nDate: 2026-02-03\nTotal: $500.00"),
        _facts("doc-b", "Name: John\nDate: 2026-02-03\nTotal: $500.00"),
    ]

    assert build_deterministic_relationship_candidates(facts) == []


def test_deterministic_candidates_are_available_without_structured_model_output():
    documents = [
        {"document_id": "doc-a", "filename": "a.pdf", "blocks": [{"block_id": "a1", "page_index": 0, "text": "Email: shared@example.test"}]},
        {"document_id": "doc-b", "filename": "b.pdf", "blocks": [{"block_id": "b1", "page_index": 0, "text": "Email: SHARED@EXAMPLE.TEST"}]},
    ]

    result = build_cross_document_correlation(documents)
    candidate = next(item for item in result["links"] if item["identifier_type"] == "EMAIL")

    assert candidate["review_status"] == "candidate"
    assert result["summary"]["confirmed_links"] == 0
    assert {ref["block_id"] for ref in candidate["evidence_refs"]} == {"a1", "b1"}


def test_repeated_correlation_reads_use_bounded_content_cache_without_mutable_leaks():
    documents = [
        {"document_id": "cache-a", "filename": "a.pdf", "blocks": [{"block_id": "a1", "page_index": 0, "text": "Email: cache@example.test"}]},
        {"document_id": "cache-b", "filename": "b.pdf", "blocks": [{"block_id": "b1", "page_index": 0, "text": "Email: CACHE@EXAMPLE.TEST"}]},
    ]

    first = build_cross_document_correlation(documents, include_timings=True)
    first["links"].clear()
    second = build_cross_document_correlation(documents, include_timings=True)

    assert first["timings_ms"]["cache_hit"] is False
    assert second["timings_ms"]["cache_hit"] is True
    assert len(second["links"]) == 1
    changed = [dict(documents[0]), {**documents[1], "blocks": [{"block_id": "b1", "page_index": 0, "text": "Email: changed@example.test"}]}]
    assert build_cross_document_correlation(changed, include_timings=True)["timings_ms"]["cache_hit"] is False

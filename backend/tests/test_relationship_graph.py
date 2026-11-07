import uuid

import pytest
from sqlalchemy import select

from app.core.security import create_access_token, hash_password
from app.main import app
from app.models.entities import Document, Investigation, RelationshipAnalysis, User, UserRole


def analysis_payload(person_mention_id: str, org_mention_id: str):
    return {
        "schema_version": "relationship_extraction_v1",
        "entities": [
            {
                "mention_id": person_mention_id,
                "canonical_entity_id": "person:rahul-sharma",
                "mention_text": "Rahul Sharma",
                "entity_type": "PERSON",
                "source_block_ids": ["blk-1"],
                "confidence": 0.95,
                "resolution": "single_document",
            },
            {
                "mention_id": org_mention_id,
                "canonical_entity_id": "org:abc-technologies",
                "mention_text": "ABC Technologies",
                "entity_type": "ORGANIZATION",
                "source_block_ids": ["blk-2"],
                "confidence": 0.9,
                "resolution": "single_document",
            },
        ],
        "relationships": [
            {
                "relationship_id": "rel-1",
                "subject_mention_id": person_mention_id,
                "object_mention_id": org_mention_id,
                "predicate": "EMPLOYED_BY",
                "relationship_kind": "explicit",
                "confidence": 0.88,
                "source_block_ids": ["blk-1"],
            },
        ],
    }


@pytest.mark.asyncio
async def test_relationship_graph_aggregates_documents_and_scopes_by_owner(client):
    async with app.state.test_sessions() as db:
        owner = User(full_name="Graph Investigator", email="graph@example.com", password_hash=hash_password("StrongPassword123"), role=UserRole.investigator)
        other = User(full_name="Other Investigator", email="other@example.com", password_hash=hash_password("StrongPassword123"), role=UserRole.investigator)
        db.add_all([owner, other])
        await db.flush()
        investigation = Investigation(case_number="DF-GRAPH01", title="Graph case", created_by_user_id=owner.id)
        db.add(investigation)
        await db.flush()
        first = Document(investigation_id=investigation.id, filename="salary.jpg", content_type="image/jpeg", size_bytes=1024, processing_status="relationship_extraction_completed")
        second = Document(investigation_id=investigation.id, filename="bank.png", content_type="image/png", size_bytes=2048, processing_status="relationship_extraction_completed")
        db.add_all([first, second])
        await db.flush()
        db.add_all([
            RelationshipAnalysis(document_id=first.id, model="test-model", payload=analysis_payload("m-1", "m-2")),
            RelationshipAnalysis(document_id=second.id, model="test-model", payload=analysis_payload("m-3", "m-4")),
        ])
        await db.commit()
        owner_id, other_id, first_id, second_id = owner.id, other.id, first.id, second.id

    client.cookies.set("dfi_access", create_access_token(str(owner_id), "investigator"), path="/api/v1")
    investigation_id = str(investigation.id)
    response = await client.get(f"/api/v1/investigations/{investigation_id}/relationships/graph")
    assert response.status_code == 200
    graph = response.json()
    assert graph["meta"]["investigation_id"] == investigation_id
    assert graph["meta"]["graph_version"] == "graph_v2"
    document_nodes = [node for node in graph["graph_nodes"] if node["kind"] == "document"]
    assert len(document_nodes) == 2
    assert {node["metadata"]["sha256"] for node in document_nodes} == {None}

    person = next(node for node in graph["nodes"] if node["id"] == "person:rahul-sharma")
    assert person["label"] == "Rahul Sharma"
    assert person["type"] == "PERSON"
    assert sorted(person["document_ids"]) == sorted([str(first_id), str(second_id)])
    assert person["resolution"] == "exact_match"

    org = next(node for node in graph["nodes"] if node["id"] == "org:abc-technologies")
    assert sorted(org["document_ids"]) == sorted([str(first_id), str(second_id)])

    assert len(graph["edges"]) == 2
    for edge in graph["edges"]:
        assert edge["type"] == "EMPLOYED_BY"
        assert edge["kind"] == "explicit"
        assert edge["confidence"] == 0.88
        assert len(edge["source_document_ids"]) == 1
        assert edge["source_block_ids"] == ["blk-1"]
    assert {edge["source"] for edge in graph["edges"]} == {"person:rahul-sharma"}
    assert {edge["target"] for edge in graph["edges"]} == {"org:abc-technologies"}
    evidence_edge = next(edge for edge in graph["graph_edges"] if edge["relationship_type"] == "employed_by")
    assert evidence_edge["basis"].startswith("Explicitly stated")
    assert evidence_edge["analyzer"] == "test-model"
    assert evidence_edge["evidence_refs"][0]["block_id"] == "blk-1"
    assert sum(edge["relationship_type"] == "mentioned_in" for edge in graph["graph_edges"]) == 4

    assert (await client.get(f"/api/v1/investigations/{investigation_id}/relationships")).json()["total"] == 2
    searched = await client.get(f"/api/v1/investigations/{investigation_id}/relationships/search?q=rahul")
    assert [item["id"] for item in searched.json()["items"]] == ["person:rahul-sharma"]
    detail = await client.get(f"/api/v1/investigations/{investigation_id}/relationships/entities/person:rahul-sharma")
    assert detail.status_code == 200
    assert len(detail.json()["relationships"]) == 2

    client.cookies.clear()
    client.cookies.set("dfi_access", create_access_token(str(other_id), "investigator"), path="/api/v1")
    assert (await client.get(f"/api/v1/investigations/{investigation_id}/relationships/graph")).status_code == 404


@pytest.mark.asyncio
async def test_relationship_graph_returns_empty_for_investigation_without_analysis(client):
    async with app.state.test_sessions() as db:
        owner = User(full_name="Empty Graph", email="empty-graph@example.com", password_hash=hash_password("StrongPassword123"), role=UserRole.investigator)
        db.add(owner)
        await db.flush()
        investigation = Investigation(case_number="DF-GRAPH02", title="Empty case", created_by_user_id=owner.id)
        db.add(investigation)
        await db.commit()
        owner_id = owner.id

    client.cookies.set("dfi_access", create_access_token(str(owner_id), "investigator"), path="/api/v1")
    response = await client.get(f"/api/v1/investigations/{uuid.UUID(int=0)}/relationships/graph")
    assert response.status_code == 404
    async with app.state.test_sessions() as db:
        investigation = await db.scalar(select(Investigation).where(Investigation.case_number == "DF-GRAPH02"))
    empty = await client.get(f"/api/v1/investigations/{investigation.id}/relationships/graph")
    assert empty.status_code == 200
    assert empty.json() == {
        "nodes": [],
        "edges": [],
        "graph_nodes": [],
        "graph_edges": [],
        "meta": {"investigation_id": str(investigation.id), "graph_version": "graph_v2"},
    }

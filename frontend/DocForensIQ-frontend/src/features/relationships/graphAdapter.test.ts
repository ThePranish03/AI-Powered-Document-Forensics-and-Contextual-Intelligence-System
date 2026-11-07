import { describe, expect, it } from 'vitest';
import { adaptRelationshipGraph } from './graphAdapter';
import type { RelationshipClaim, RelationshipDocument, RelationshipEntity } from './RelationshipExplorer';

const documents: RelationshipDocument[] = [{
  id: 'doc-1',
  filename: 'invoice.png',
  content_type: 'image/png',
  size_bytes: 12,
  processing_status: 'ocr_completed',
  created_at: '2025-01-01T00:00:00Z',
}];
const entities: RelationshipEntity[] = [{
  id: 'entity-1',
  label: 'Acme',
  type: 'ORGANIZATION',
  document_ids: ['doc-1'],
  resolution: 'candidate',
}];
const claims: RelationshipClaim[] = [];

describe('adaptRelationshipGraph', () => {
  it('adapts graph v2 nodes and preserves evidence provenance while dropping invalid references', () => {
    const graph = adaptRelationshipGraph(documents, entities, claims, [
      { id: 'document:doc-1', kind: 'document', label: 'invoice.png', document_id: 'doc-1' },
      { id: 'entity-1', kind: 'entity', label: 'Acme', type: 'ORGANIZATION' },
      { id: 'unsupported', kind: 'account', label: 'Unknown' },
    ], [
      {
        id: 'mention-1',
        source: 'document:doc-1',
        target: 'entity-1',
        relationship_type: 'mentioned_in',
        status: 'candidate',
        evidence_refs: [{ document_id: 'doc-1', block_id: 'p1-b1', quoted_text: 'Acme' }],
      },
      { id: 'orphan', source: 'missing', target: 'entity-1' },
    ]);

    expect(graph.nodes.map((node) => node.kind)).toEqual(['document', 'entity']);
    expect(graph.edges).toHaveLength(1);
    expect(graph.edges[0].evidenceRefs[0]).toMatchObject({ document_id: 'doc-1', block_id: 'p1-b1' });
    expect(graph.ignoredNodeCount).toBe(1);
    expect(graph.danglingEdgeCount).toBe(1);
  });

  it('creates document-to-entity evidence links when using the legacy graph response', () => {
    const graph = adaptRelationshipGraph(documents, entities, claims);

    expect(graph.edges).toHaveLength(1);
    expect(graph.edges[0]).toMatchObject({
      source: 'document:doc-1',
      target: 'entity-1',
      relationshipType: 'mentioned_in',
    });
  });
});

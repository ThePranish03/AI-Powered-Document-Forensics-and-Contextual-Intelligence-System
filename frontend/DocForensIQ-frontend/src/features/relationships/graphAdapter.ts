import type { RelationshipClaim, RelationshipDocument, RelationshipEntity } from './RelationshipExplorer';

export type GraphKind = 'document' | 'entity' | 'finding' | 'event';

export interface GraphNode {
  id: string;
  kind: GraphKind;
  label: string;
  type: string;
  status: string;
  confidence: number | null;
  metadata: Record<string, unknown>;
  documentIds: string[];
}

export interface GraphEdge {
  id: string;
  source: string;
  target: string;
  relationshipType: string;
  label: string;
  status: string;
  confidence: number | null;
  basis: string;
  analyzer: string;
  evidenceRefs: Array<Record<string, unknown>>;
}

export interface GraphAdapterResult {
  nodes: GraphNode[];
  edges: GraphEdge[];
  ignoredNodeCount: number;
  danglingEdgeCount: number;
}

export interface ApiGraphNode {
  id: string;
  kind?: string;
  label: string;
  type?: string;
  status?: string;
  confidence?: number | null;
  metadata?: Record<string, unknown>;
  document_id?: string;
  document_ids?: string[];
  resolution?: string;
}

export interface ApiGraphEdge {
  id: string;
  source: string;
  target: string;
  relationship_type?: string;
  type?: string;
  label?: string;
  status?: string;
  confidence?: number | null;
  basis?: string;
  analyzer?: string;
  evidence_refs?: Array<Record<string, unknown>>;
  source_document_ids?: string[];
  source_block_ids?: string[];
}

const knownKinds = new Set<GraphKind>(['document', 'entity', 'finding', 'event']);

export function adaptRelationshipGraph(
  documents: RelationshipDocument[],
  entities: RelationshipEntity[],
  claims: RelationshipClaim[],
  apiNodes?: ApiGraphNode[],
  apiEdges?: ApiGraphEdge[],
): GraphAdapterResult {
  const sourceNodes: ApiGraphNode[] = apiNodes ?? [
    ...documents.map((document) => ({
      id: `document:${document.id}`,
      kind: 'document',
      label: document.filename,
      type: document.content_type,
      status: document.processing_status,
      document_id: document.id,
      metadata: { sha256: document.sha256, size_bytes: document.size_bytes },
    })),
    ...entities.map((entity) => ({
      id: entity.id,
      kind: 'entity',
      label: entity.label,
      type: entity.type,
      status: 'candidate',
      document_ids: entity.document_ids,
      resolution: entity.resolution,
    })),
  ];
  const nodes: GraphNode[] = [];
  let ignoredNodeCount = 0;
  for (const item of sourceNodes) {
    const kind = item.kind;
    if (!kind || !knownKinds.has(kind as GraphKind)) {
      ignoredNodeCount += 1;
      continue;
    }
    nodes.push({
      id: item.id,
      kind: kind as GraphKind,
      label: item.label,
      type: item.type ?? kind,
      status: item.status ?? 'candidate',
      confidence: typeof item.confidence === 'number' ? item.confidence : null,
      metadata: item.metadata ?? {},
      documentIds: item.document_ids ?? (item.document_id ? [item.document_id] : []),
    });
  }
  const edgesFromApi: ApiGraphEdge[] = apiEdges ?? claims.map((claim) => ({
    id: claim.id,
    source: claim.source,
    target: claim.target,
    relationship_type: claim.type,
    label: claim.type.replaceAll('_', ' ').toLowerCase(),
    status: 'candidate',
    confidence: claim.confidence,
    basis: claim.kind === 'explicit' ? 'Explicitly stated in source blocks.' : 'Inferred from source blocks.',
    analyzer: 'relationship_extraction',
    source_document_ids: claim.source_document_ids,
    source_block_ids: claim.source_block_ids,
  }));
  if (!apiEdges) {
    for (const entity of entities) {
      for (const documentId of entity.document_ids) {
        edgesFromApi.push({
          id: `mention:${documentId}:${entity.id}`,
          source: `document:${documentId}`,
          target: entity.id,
          relationship_type: 'mentioned_in',
          label: 'mentioned in',
          status: 'candidate',
          basis: 'Entity mention was extracted from OCR source blocks.',
          analyzer: 'relationship_extraction',
          source_document_ids: [documentId],
        });
      }
    }
  }
  const ids = new Set(nodes.map((node) => node.id));
  const edges: GraphEdge[] = [];
  let danglingEdgeCount = 0;
  for (const item of edgesFromApi) {
    if (!ids.has(item.source) || !ids.has(item.target)) {
      danglingEdgeCount += 1;
      continue;
    }
    edges.push({
      id: item.id,
      source: item.source,
      target: item.target,
      relationshipType: item.relationship_type ?? item.type ?? 'related',
      label: item.label ?? item.relationship_type ?? item.type ?? 'related',
      status: item.status ?? 'candidate',
      confidence: typeof item.confidence === 'number' ? item.confidence : null,
      basis: item.basis ?? 'Basis not provided.',
      analyzer: item.analyzer ?? 'unknown',
      evidenceRefs: item.evidence_refs ?? (item.source_document_ids ?? []).map((documentId) => ({
        document_id: documentId,
        source_block_ids: item.source_block_ids ?? [],
      })),
    });
  }
  return { nodes, edges, ignoredNodeCount, danglingEdgeCount };
}

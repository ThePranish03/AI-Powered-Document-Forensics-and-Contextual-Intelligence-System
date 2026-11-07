import { useMemo, useState } from 'react';
import { ArrowUpRight, FileText, Network, Search, ZoomIn, ZoomOut } from 'lucide-react';
import type { RelationshipClaim, RelationshipDocument, RelationshipEntity } from './RelationshipExplorer';
import { adaptRelationshipGraph, type ApiGraphEdge, type ApiGraphNode, type GraphEdge, type GraphKind, type GraphNode } from './graphAdapter';
import './relationships.css';

interface Props {
  documents: RelationshipDocument[];
  entities: RelationshipEntity[];
  claims: RelationshipClaim[];
  graphNodes?: ApiGraphNode[];
  graphEdges?: ApiGraphEdge[];
  search: string;
  onSearch: (value: string) => void;
  onOpenDocument: (documentId: string) => void;
  onGoToDocuments?: () => void;
}

const kindOrder: GraphKind[] = ['document', 'entity', 'finding', 'event'];
const kindLabels: Record<GraphKind, string> = {
  document: 'Documents',
  entity: 'Entities',
  finding: 'Findings',
  event: 'Timeline events',
};
const kindDetailLabels: Record<GraphKind, string> = {
  document: 'DOCUMENT',
  entity: 'ENTITY',
  finding: 'FINDING',
  event: 'TIMELINE EVENT',
};
const kindSingular: Record<GraphKind, string> = {
  document: 'Document',
  entity: 'Entity',
  finding: 'Finding',
  event: 'Timeline event',
};
const canvasWidth = 1100;
const nodeWidth = 206;
const nodeHeight = 58;
const columnX: Record<GraphKind, number> = {
  document: 150,
  entity: 420,
  finding: 690,
  event: 960,
};

function humanize(value: string) {
  return value.replaceAll('_', ' ').toLowerCase();
}

function short(value: string, limit = 27) {
  return value.length > limit ? `${value.slice(0, limit - 1)}…` : value;
}

function positionNodes(nodes: GraphNode[]) {
  const grouped = new Map(kindOrder.map((kind) => [kind, nodes.filter((node) => node.kind === kind)]));
  const height = Math.max(360, ...[...grouped.values()].map((group) => group.length * 106 + 100));
  const positions = new Map<string, { x: number; y: number }>();
  for (const kind of kindOrder) {
    const group = grouped.get(kind) ?? [];
    group.forEach((node, index) => {
      positions.set(node.id, {
        x: columnX[kind],
        y: group.length === 1 ? height / 2 : 52 + index * (height - 104) / (group.length - 1),
      });
    });
  }
  return { positions, height };
}

function evidenceLabel(ref: Record<string, unknown>, documents: RelationshipDocument[]) {
  if (typeof ref.filename === 'string') return ref.filename;
  if (typeof ref.document_id === 'string') {
    return documents.find((document) => document.id === ref.document_id)?.filename ?? 'Source document';
  }
  return 'Evidence reference';
}

export default function EvidenceGraphView({
  documents, entities, claims, graphNodes, graphEdges, search, onSearch, onOpenDocument, onGoToDocuments,
}: Props) {
  const [kindFilter, setKindFilter] = useState('All node types');
  const [relationshipFilter, setRelationshipFilter] = useState('All relationships');
  const [statusFilter, setStatusFilter] = useState('All statuses');
  const [confidenceFilter, setConfidenceFilter] = useState('Any confidence');
  const [evidenceOnly, setEvidenceOnly] = useState(false);
  const [selectedNodeId, setSelectedNodeId] = useState<string | null>(null);
  const [selectedEdgeId, setSelectedEdgeId] = useState<string | null>(null);
  const [zoom, setZoom] = useState(100);
  const [tableView, setTableView] = useState(false);

  const adapted = useMemo(
    () => adaptRelationshipGraph(documents, entities, claims, graphNodes, graphEdges),
    [documents, entities, claims, graphNodes, graphEdges],
  );
  const needle = search.trim().toLocaleLowerCase();
  const relationshipTypes = [...new Set(adapted.edges.map((edge) => edge.relationshipType))].sort();
  const statuses = [...new Set([
    ...adapted.nodes.map((node) => node.status),
    ...adapted.edges.map((edge) => edge.status),
  ])].sort();
  const eligibleNodes = adapted.nodes.filter((node) => {
    const matchesKind = kindFilter === 'All node types' || node.kind === kindFilter;
    const matchesConfidence = confidenceFilter === 'Any confidence'
      || (node.confidence !== null && node.confidence >= Number(confidenceFilter));
    const matchesEvidence = !evidenceOnly || adapted.edges.some((edge) =>
      (edge.source === node.id || edge.target === node.id) && edge.evidenceRefs.length > 0);
    return matchesKind && matchesConfidence && matchesEvidence;
  });
  const eligibleNodeIds = new Set(eligibleNodes.map((node) => node.id));
  const eligibleEdges = adapted.edges.filter((edge) => {
    const matchesRelationship = relationshipFilter === 'All relationships'
      || edge.relationshipType === relationshipFilter;
    const matchesStatus = statusFilter === 'All statuses' || edge.status === statusFilter;
    const matchesConfidence = confidenceFilter === 'Any confidence'
      || (edge.confidence !== null && edge.confidence >= Number(confidenceFilter));
    return matchesRelationship && matchesStatus && matchesConfidence
      && (!evidenceOnly || edge.evidenceRefs.length > 0)
      && eligibleNodeIds.has(edge.source) && eligibleNodeIds.has(edge.target);
  });
  const matchingNodeIds = new Set(eligibleNodes.filter((node) =>
    !needle || `${node.label} ${node.type} ${node.status}`.toLocaleLowerCase().includes(needle),
  ).map((node) => node.id));
  const matchingEdges = eligibleEdges.filter((edge) =>
    !needle || `${edge.label} ${edge.relationshipType} ${edge.status} ${edge.basis}`.toLocaleLowerCase().includes(needle),
  );
  const matchingEdgeIds = new Set(matchingEdges.flatMap((edge) => [edge.source, edge.target]));
  const visibleEdges = eligibleEdges.filter((edge) =>
    !needle || matchingEdges.includes(edge) || matchingNodeIds.has(edge.source) || matchingNodeIds.has(edge.target),
  );
  const visibleNodeIds = new Set([
    ...matchingNodeIds,
    ...visibleEdges.flatMap((edge) => [edge.source, edge.target]),
    ...matchingEdgeIds,
  ]);
  const visibleNodes = eligibleNodes.filter((node) => visibleNodeIds.has(node.id));
  const { positions, height } = positionNodes(visibleNodes);
  const selectedNode = visibleNodes.find((node) => node.id === selectedNodeId)
    ?? (needle ? visibleNodes.find((node) => node.kind === 'entity') ?? null : null);
  const selectedEdge = adapted.edges.find((edge) => edge.id === selectedEdgeId) ?? null;

  function chooseNode(node: GraphNode) {
    setSelectedNodeId(node.id);
    setSelectedEdgeId(null);
  }

  function chooseEdge(edge: GraphEdge) {
    setSelectedEdgeId(edge.id);
    setSelectedNodeId(null);
  }

  function clearSelection() {
    setSelectedNodeId(null);
    setSelectedEdgeId(null);
  }

  const hasAnyData = adapted.nodes.length > 0;
  const filtersActive = kindFilter !== 'All node types'
    || relationshipFilter !== 'All relationships'
    || statusFilter !== 'All statuses'
    || confidenceFilter !== 'Any confidence'
    || evidenceOnly
    || needle !== '';

  function resetFilters() {
    setKindFilter('All node types');
    setRelationshipFilter('All relationships');
    setStatusFilter('All statuses');
    setConfidenceFilter('Any confidence');
    setEvidenceOnly(false);
    onSearch('');
  }

  return (
    <section className="panel relationship-explorer evidence-graph-panel" aria-label="Evidence relationship graph">
      <div className="relationship-explorer-heading">
        <div>
          <div className="relationship-explorer-title"><Network size={17} /><h2>Evidence Relationship Graph</h2></div>
          <p>Documents, extracted entities, findings, and timeline events. Automated results remain candidates and source links are preserved.</p>
        </div>
        <span className="badge blue">{visibleNodes.length} nodes / {visibleEdges.length} evidence links</span>
      </div>

      <div className="relationship-toolbar">
        <label className="relationship-search">
          <Search size={15} />
          <span className="sr-only">Search entities and evidence graph</span>
          <input value={search} onChange={(event) => onSearch(event.target.value)} placeholder="Search nodes and relationships…" />
        </label>
        <label className="relationship-filter">Node type
          <select value={kindFilter} onChange={(event) => setKindFilter(event.target.value)}>
            <option>All node types</option>
            {kindOrder.map((kind) => <option key={kind} value={kind}>{kindLabels[kind]}</option>)}
          </select>
        </label>
        <label className="relationship-filter">Relationship
          <select value={relationshipFilter} onChange={(event) => setRelationshipFilter(event.target.value)}>
            <option>All relationships</option>
            {relationshipTypes.map((type) => <option key={type} value={type}>{humanize(type)}</option>)}
          </select>
        </label>
        <label className="relationship-filter">Status
          <select value={statusFilter} onChange={(event) => setStatusFilter(event.target.value)}>
            <option>All statuses</option>
            {statuses.map((status) => <option key={status} value={status}>{humanize(status)}</option>)}
          </select>
        </label>
        <label className="relationship-filter">Minimum confidence
          <select value={confidenceFilter} onChange={(event) => setConfidenceFilter(event.target.value)}>
            <option>Any confidence</option>
            <option value="0.7">70% (uncalibrated)</option>
            <option value="0.85">85% (uncalibrated)</option>
          </select>
        </label>
        <label className="evidence-graph-check"><input type="checkbox" checked={evidenceOnly} onChange={(event) => setEvidenceOnly(event.target.checked)} /> Evidence-backed only</label>
        <div className="relationship-controls" role="group" aria-label="Graph controls">
          <button type="button" className="icon-button" aria-label="Zoom out" onClick={() => setZoom((value) => Math.max(70, value - 10))}><ZoomOut size={15} /></button>
          <span aria-live="polite">{zoom}%</span>
          <button type="button" className="icon-button" aria-label="Zoom in" onClick={() => setZoom((value) => Math.min(140, value + 10))}><ZoomIn size={15} /></button>
          <button type="button" className="button secondary small" onClick={() => setZoom(100)}>Fit</button>
          <button type="button" className="button secondary small" aria-pressed={tableView} onClick={() => setTableView((value) => !value)}>{tableView ? 'Graph' : 'Table'}</button>
        </div>
      </div>

      {adapted.ignoredNodeCount > 0 || adapted.danglingEdgeCount > 0
        ? <p className="evidence-graph-warning" role="status">
          {adapted.ignoredNodeCount ? `${adapted.ignoredNodeCount} unsupported node(s) were omitted. ` : ''}
          {adapted.danglingEdgeCount ? `${adapted.danglingEdgeCount} relationship(s) with missing endpoints were omitted.` : ''}
        </p>
        : null}

      <div className="relationship-graph-layout evidence-graph-layout">
        {tableView ? (
          <div className="evidence-graph-tables">
            {kindOrder.map((kind) => {
              const items = visibleNodes.filter((node) => node.kind === kind);
              return items.length > 0 && <section key={kind} aria-label={kindLabels[kind]}>
                <h3>{kindLabels[kind]} <span>{items.length}</span></h3>
                <ul>{items.map((node) => <li key={node.id}><button type="button" onClick={() => chooseNode(node)}>{node.label}<small>{humanize(node.type)} · {humanize(node.status)}</small></button></li>)}</ul>
              </section>;
            })}
            <section aria-label="Relationships"><h3>Relationships <span>{visibleEdges.length}</span></h3>
              <ul>{visibleEdges.map((edge) => <li key={edge.id}><button type="button" onClick={() => chooseEdge(edge)}>{edge.label}<small>{edge.source} → {edge.target} · {humanize(edge.status)}</small></button></li>)}</ul>
            </section>
          </div>
        ) : (
          <div className="relationship-canvas-scroll relationship-graph-scroll evidence-graph-scroll" tabIndex={0} aria-label="Scrollable evidence relationship graph">
            {visibleNodes.length ? <svg
              className="evidence-graph-svg"
              width={canvasWidth * zoom / 100}
              height={height * zoom / 100}
              viewBox={`0 0 ${canvasWidth} ${height}`}
              role="group"
              aria-label="Interactive graph. Select a node or relationship to inspect details."
            >
              <title>Evidence relationship graph</title>
              {visibleEdges.map((edge) => {
                const source = positions.get(edge.source);
                const target = positions.get(edge.target);
                if (!source || !target) return null;
                const dx = target.x - source.x;
                const dy = target.y - source.y;
                const clip = 1 / Math.max(Math.abs(dx) / (nodeWidth / 2), Math.abs(dy) / (nodeHeight / 2), 1);
                const sourceX = source.x + dx * clip;
                const sourceY = source.y + dy * clip;
                const targetX = target.x - dx * clip;
                const targetY = target.y - dy * clip;
                const midX = (sourceX + targetX) / 2;
                const midY = (sourceY + targetY) / 2;
                return <g key={edge.id} className={`evidence-graph-edge ${edge.status === 'candidate' ? 'is-candidate' : ''} ${selectedEdgeId === edge.id ? 'is-selected' : ''}`}>
                  <line x1={sourceX} y1={sourceY} x2={targetX} y2={targetY} />
                  <path d={`M ${sourceX} ${sourceY} L ${targetX} ${targetY}`} role="button" tabIndex={0}
                    aria-label={`${edge.label}: ${edge.source} to ${edge.target}`}
                    onClick={() => chooseEdge(edge)}
                    onKeyDown={(event) => { if (event.key === 'Enter' || event.key === ' ') { event.preventDefault(); chooseEdge(edge); } }} />
                  <g className="evidence-graph-edge-label" aria-hidden="true">
                    <rect x={midX - 56} y={midY - 11} width="112" height="22" rx="5" />
                    <text x={midX} y={midY + 3}>{short(humanize(edge.label), 20)}</text>
                  </g>
                </g>;
              })}
              {visibleNodes.map((node) => {
                const point = positions.get(node.id)!;
                return <g key={node.id}
                  className={`evidence-graph-node node-${node.kind} ${selectedNodeId === node.id ? 'is-selected' : ''}`}
                  role="button" tabIndex={0} aria-label={`${kindSingular[node.kind]}: ${node.label}; ${humanize(node.type)}; ${humanize(node.status)}`}
                  transform={`translate(${point.x - nodeWidth / 2} ${point.y - nodeHeight / 2})`}
                  onClick={() => chooseNode(node)}
                  onKeyDown={(event) => { if (event.key === 'Enter' || event.key === ' ') { event.preventDefault(); chooseNode(node); } }}>
                  <rect width={nodeWidth} height={nodeHeight} rx="8" />
                  <text x="12" y="24" className="evidence-graph-node-label">{short(node.label, 30)}</text>
                  <text x="12" y="42" className="evidence-graph-node-meta">{short(`${humanize(node.type)} · ${humanize(node.status)}`, 34)}</text>
                  <title>{`${node.label} · ${humanize(node.type)} · ${humanize(node.status)}`}</title>
                </g>;
              })}
            </svg> : <div className="relationship-empty">
              <Network size={24} />
              {hasAnyData ? <>
                <h3>No matching graph nodes</h3>
                <p>Your filters or search hid every node. Clear them to see the available evidence.</p>
                {filtersActive && <button type="button" className="button secondary small evidence-graph-empty-action" onClick={resetFilters}>Clear filters</button>}
              </> : <>
                <h3>No evidence connected yet</h3>
                <p>Connections appear here after documents are uploaded and OCR plus relationship analysis finish. Automated links stay candidates until you review them.</p>
                {onGoToDocuments && <button type="button" className="button secondary small evidence-graph-empty-action" onClick={onGoToDocuments}>Go to documents</button>}
              </>}
            </div>}
          </div>
        )}

        <aside className="relationship-side evidence-graph-details" aria-live="polite" aria-label="Selected graph item details">
          {(selectedNode || selectedEdge) && <button type="button" className="button secondary small evidence-graph-clear" onClick={clearSelection}>Clear selection</button>}
          {selectedNode ? (
            <div className="relationship-detail relationship-side-card">
              <div className="relationship-detail-heading"><div><p>{kindDetailLabels[selectedNode.kind]} DETAILS</p><h3>{selectedNode.label}</h3></div>
                <span className="badge blue"><span className="status-dot" />{humanize(selectedNode.status)}</span></div>
              <div className="relationship-detail-grid">
                <span>Type</span><strong>{humanize(selectedNode.type)}</strong>
                <span>Confidence</span><strong>{selectedNode.confidence === null ? 'Not provided' : `${Math.round(selectedNode.confidence * 100)}% · uncalibrated`}</strong>
                <span>Connections</span><strong>{adapted.edges.filter((edge) => edge.source === selectedNode.id || edge.target === selectedNode.id).length}</strong>
              </div>
              {Object.entries(selectedNode.metadata).map(([key, value]) => <div className="evidence-graph-metadata" key={key}><span>{humanize(key)}</span><strong>{typeof value === 'string' || typeof value === 'number' || typeof value === 'boolean' ? String(value) : JSON.stringify(value)}</strong></div>)}
              {selectedNode.documentIds.map((documentId) => {
                const document = documents.find((item) => item.id === documentId);
                return document && <button type="button" key={documentId} className="text-link" onClick={() => onOpenDocument(documentId)}><FileText size={12} />{short(document.filename, 28)}<ArrowUpRight size={12} /></button>;
              })}
              {adapted.edges.some((edge) => edge.source === selectedNode.id || edge.target === selectedNode.id) && <div className="relationship-side-docs">
                <p className="evidence-graph-section-label">KEY CONNECTIONS</p>
                {adapted.edges.filter((edge) => edge.source === selectedNode.id || edge.target === selectedNode.id).map((edge) => {
                  const otherId = edge.source === selectedNode.id ? edge.target : edge.source;
                  const other = adapted.nodes.find((node) => node.id === otherId);
                  return <button type="button" key={edge.id} className="text-link"
                    aria-label={`${edge.label} ${other?.label ?? otherId}`}
                    onClick={() => chooseEdge(edge)}>
                    <Network size={12} /> {short(`${edge.label} ${other?.label ?? otherId}`, 35)} <ArrowUpRight size={12} />
                  </button>;
                })}
              </div>}
              {selectedNode.kind === 'document' && selectedNode.documentIds[0] && <button type="button" className="button secondary small evidence-graph-open" onClick={() => onOpenDocument(selectedNode.documentIds[0])}>Open source document</button>}
            </div>
          ) : selectedEdge ? (
            <div className="relationship-detail relationship-side-card">
              <div className="relationship-detail-heading"><div><p>RELATIONSHIP DETAILS</p><h3>{selectedEdge.label}</h3></div><span className="badge blue"><span className="status-dot" />{humanize(selectedEdge.status)}</span></div>
              <div className="relationship-detail-grid">
                <span>Relationship</span><strong>{humanize(selectedEdge.relationshipType)}</strong>
                <span>From</span><strong>{adapted.nodes.find((node) => node.id === selectedEdge.source)?.label ?? selectedEdge.source}</strong>
                <span>To</span><strong>{adapted.nodes.find((node) => node.id === selectedEdge.target)?.label ?? selectedEdge.target}</strong>
                <span>Confidence</span><strong>{selectedEdge.confidence === null ? 'Not provided' : `${Math.round(selectedEdge.confidence * 100)}% · uncalibrated`}</strong>
                <span>Analyzer</span><strong>{selectedEdge.analyzer}</strong>
              </div>
              <p className="relationship-side-empty">{selectedEdge.basis}</p>
              {selectedEdge.evidenceRefs.length
                ? <ul className="evidence-reference-list">{selectedEdge.evidenceRefs.map((ref, index) => {
                  const documentId = typeof ref.document_id === 'string' ? ref.document_id : null;
                  return <li key={`${selectedEdge.id}:${index}`}>
                    <b>{evidenceLabel(ref, documents)}</b>
                    {typeof ref.page_number === 'number' && <span>Page {ref.page_number + 1}</span>}
                    {typeof ref.block_id === 'string' && <span>Block {ref.block_id}</span>}
                    {typeof ref.quoted_text === 'string' && <q>{short(ref.quoted_text, 140)}</q>}
                    {documentId && <button type="button" className="text-link" aria-label={evidenceLabel(ref, documents)} onClick={() => onOpenDocument(documentId)}>Open source <ArrowUpRight size={12} /></button>}
                  </li>;
                })}</ul>
                : <p className="relationship-side-empty">No evidence references were provided for this relationship.</p>}
            </div>
          ) : <div className="relationship-detail relationship-side-card"><h3>Inspect graph evidence</h3><p className="relationship-side-empty">Select a node or relationship to review its status, provenance, and source references.</p></div>}
          <div className="evidence-graph-legend" aria-label="Node type legend">{kindOrder.map((kind) => <span key={kind} className={`legend-${kind}`}><i />{kindLabels[kind]}</span>)}</div>
        </aside>
      </div>
    </section>
  );
}

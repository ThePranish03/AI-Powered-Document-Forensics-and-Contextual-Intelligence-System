import { useMemo, useState } from 'react';
import {
  ArrowLeftRight, ArrowUpRight, Box, Building2, CheckCircle2, FileCheck2, FileText,
  GitBranch, Landmark, MapPin, Network, Receipt, Search, Shapes, UserRound, ZoomIn, ZoomOut,
} from 'lucide-react';
import type { RelationshipClaim, RelationshipDocument, RelationshipEntity } from './RelationshipExplorer';
import './relationships.css';

interface Props {
  documents: RelationshipDocument[];
  entities: RelationshipEntity[];
  claims: RelationshipClaim[];
  search: string;
  onSearch: (value: string) => void;
  onOpenDocument: (documentId: string) => void;
}

const CANVAS_W = 880;
const CANVAS_H = 640;
const CENTER_X = CANVAS_W / 2;
const CENTER_Y = CANVAS_H / 2;
const DOC_RADIUS = 188;
const ENTITY_RADIUS = 296;
const CENTER_NODE_R = 46;
const SATELLITE_NODE_R = 27;

const ENTITY_ICONS: Record<string, typeof UserRound> = {
  PERSON: UserRound,
  ORGANIZATION: Building2,
  ADDRESS: MapPin,
  ACCOUNT: Landmark,
  DOCUMENT: FileText,
  INVOICE: Receipt,
  CONTRACT: FileCheck2,
  TRANSACTION: ArrowLeftRight,
  ASSET: Box,
};

function humanize(value: string) {
  return value.replaceAll('_', ' ').toLowerCase();
}

function truncate(value: string, length: number) {
  return value.length > length ? `${value.slice(0, length - 1)}…` : value;
}

function ringPosition(index: number, count: number, radius: number) {
  if (count === 1) return { x: CENTER_X, y: CENTER_Y - radius };
  const angle = -Math.PI / 2 + (index * 2 * Math.PI) / count;
  return { x: CENTER_X + radius * Math.cos(angle), y: CENTER_Y + radius * Math.sin(angle) };
}

function edgeEndpoint(fromX: number, fromY: number, toX: number, toY: number, inset: number) {
  const dx = toX - fromX;
  const dy = toY - fromY;
  const length = Math.hypot(dx, dy) || 1;
  return { x: toX - (dx / length) * inset, y: toY - (dy / length) * inset };
}

interface Connection {
  key: string;
  kind: 'document' | 'entity';
  targetId: string;
  label: string;
  predicate: string;
  claimKind: string | null;
  confidence: number | null;
  documentIds: string[];
  blockIds: string[];
}

export default function RelationshipGraphView({ documents, entities, claims, search, onSearch, onOpenDocument }: Props) {
  const [entityType, setEntityType] = useState('All types');
  const [predicateFilter, setPredicateFilter] = useState('All predicates');
  const [confidence, setConfidence] = useState('Any confidence');
  const [zoom, setZoom] = useState(100);
  const [selectedId, setSelectedId] = useState<string | null>(null);

  const entityTypes = useMemo(() => [...new Set(entities.map((entity) => entity.type))].sort(), [entities]);
  const predicates = useMemo(() => [...new Set(claims.map((claim) => claim.type))].sort(), [claims]);
  const needle = search.trim().toLocaleLowerCase();

  const filteredEntities = entities.filter((entity) =>
    (entityType === 'All types' || entity.type === entityType)
    && (!needle || `${entity.label} ${entity.type}`.toLocaleLowerCase().includes(needle)),
  );
  const visibleEntityIds = new Set(filteredEntities.map((entity) => entity.id));

  const claimPassesFilters = (claim: RelationshipClaim) =>
    (predicateFilter === 'All predicates' || claim.type === predicateFilter)
    && (confidence === 'Any confidence' || (claim.confidence !== null && claim.confidence >= Number(confidence)))
    && visibleEntityIds.has(claim.source) && visibleEntityIds.has(claim.target);

  const fallbackId = useMemo(() => {
    const ranked = [...filteredEntities].sort((a, b) => b.document_ids.length - a.document_ids.length);
    return ranked[0]?.id ?? null;
  }, [filteredEntities]);
  const centerId = selectedId && visibleEntityIds.has(selectedId) ? selectedId : fallbackId;
  const center = filteredEntities.find((entity) => entity.id === centerId) ?? null;

  const centerClaims = claims.filter((claim) => claimPassesFilters(claim) && (claim.source === centerId || claim.target === centerId));
  const neighborClaims = new Map<string, RelationshipClaim[]>();
  for (const claim of centerClaims) {
    const otherId = claim.source === centerId ? claim.target : claim.source;
    const list = neighborClaims.get(otherId) ?? [];
    list.push(claim);
    neighborClaims.set(otherId, list);
  }
  const neighbors = filteredEntities.filter((entity) => neighborClaims.has(entity.id));
  const linkedDocuments = documents.filter((document) => center?.document_ids.includes(document.id));

  const docPositions = new Map(linkedDocuments.map((document, index) => [document.id, ringPosition(index, linkedDocuments.length, DOC_RADIUS)]));
  const neighborPositions = new Map(neighbors.map((entity, index) => [entity.id, ringPosition(index, neighbors.length, ENTITY_RADIUS)]));

  const connections: Connection[] = [
    ...linkedDocuments.map((document) => ({
      key: `doc-${document.id}`,
      kind: 'document' as const,
      targetId: document.id,
      label: document.filename,
      predicate: 'mentioned in',
      claimKind: null,
      confidence: null,
      documentIds: [document.id],
      blockIds: [],
    })),
    ...neighbors.map((entity) => {
      const list = neighborClaims.get(entity.id) ?? [];
      const best = [...list].sort((a, b) => (b.confidence ?? 0) - (a.confidence ?? 0))[0];
      return {
        key: `entity-${entity.id}`,
        kind: 'entity' as const,
        targetId: entity.id,
        label: entity.label,
        predicate: humanize(best?.type ?? 'associated with'),
        claimKind: best?.kind ?? null,
        confidence: best?.confidence ?? null,
        documentIds: [...new Set(list.flatMap((claim) => claim.source_document_ids))],
        blockIds: [...new Set(list.flatMap((claim) => claim.source_block_ids))],
      };
    }),
  ];

  const CenterIcon = center ? ENTITY_ICONS[center.type] ?? Shapes : Shapes;
  const relationshipCount = claims.filter((claim) => claim.source === centerId || claim.target === centerId).length;

  if (!entities.length) {
    return <section className="panel relationship-explorer relationship-graph-panel" aria-label="Document relationship graph">
      <div className="relationship-empty">
        <GitBranch size={24} />
        <h3>No extracted connections yet</h3>
        <p>Connections appear after document OCR and relationship extraction complete. Confirm that the OCR worker and relationship extraction are configured, then upload an authorized document.</p>
      </div>
    </section>;
  }

  return (
    <section className="panel relationship-explorer relationship-graph-panel" aria-label="Document relationship graph">
      <div className="relationship-explorer-heading">
        <div>
          <div className="relationship-explorer-title"><Network size={17} /><h2>Document Relationship Graph</h2></div>
          <p>Entities and their relationships across documents. Select an entity to recenter the graph; every connection cites its source blocks.</p>
        </div>
        <span className="badge blue">{filteredEntities.length} entities / {centerClaims.length} relationships</span>
      </div>

      <div className="relationship-toolbar">
        <label className="relationship-search">
          <Search size={15} />
          <span className="sr-only">Search entities</span>
          <input value={search} onChange={(event) => onSearch(event.target.value)} placeholder="Search entities…" />
        </label>
        <label className="relationship-filter">Focus entity
          <select value={centerId ?? ''} onChange={(event) => setSelectedId(event.target.value)}>
            {filteredEntities.map((entity) => <option key={entity.id} value={entity.id}>{entity.label} · {entity.type}</option>)}
          </select>
        </label>
        <label className="relationship-filter">Entity type
          <select value={entityType} onChange={(event) => setEntityType(event.target.value)}>
            <option>All types</option>
            {entityTypes.map((type) => <option key={type}>{type}</option>)}
          </select>
        </label>
        <label className="relationship-filter">Relationship
          <select value={predicateFilter} onChange={(event) => setPredicateFilter(event.target.value)}>
            <option>All predicates</option>
            {predicates.map((type) => <option key={type} value={type}>{humanize(type)}</option>)}
          </select>
        </label>
        <label className="relationship-filter">Minimum model confidence
          <select value={confidence} onChange={(event) => setConfidence(event.target.value)}>
            <option>Any confidence</option>
            <option value="0.7">70% (uncalibrated)</option>
            <option value="0.85">85% (uncalibrated)</option>
          </select>
        </label>
        <div className="relationship-controls" aria-label="Graph controls">
          <button type="button" className="icon-button" aria-label="Zoom out" onClick={() => setZoom((value) => Math.max(70, value - 10))}><ZoomOut size={15} /></button>
          <span aria-live="polite">{zoom}%</span>
          <button type="button" className="icon-button" aria-label="Zoom in" onClick={() => setZoom((value) => Math.min(130, value + 10))}><ZoomIn size={15} /></button>
          <button type="button" className="button secondary small" onClick={() => setZoom(100)}>Fit</button>
        </div>
      </div>

      <div className="relationship-graph-layout">
        <div className="relationship-canvas-scroll relationship-graph-scroll" tabIndex={0} aria-label="Radial relationship graph">
          <div className="relationship-graph-zoom" style={{ height: CANVAS_H * zoom / 100, minHeight: CANVAS_H * zoom / 100 }}>
            <svg
              className="relationship-graph-svg"
              viewBox={`0 0 ${CANVAS_W} ${CANVAS_H}`}
              style={{ transform: `scale(${zoom / 100})`, transformOrigin: 'top left' }}
              role="img"
              aria-label={center ? `Relationship graph centered on ${center.label}` : 'Relationship graph'}
            >
              <defs>
                <marker id="graph-arrow" markerWidth="8" markerHeight="8" refX="7" refY="4" orient="auto"><path d="M0,0 L8,4 L0,8 z" /></marker>
              </defs>

              {linkedDocuments.map((document) => {
                const point = docPositions.get(document.id)!;
                const end = edgeEndpoint(CENTER_X, CENTER_Y, point.x, point.y, SATELLITE_NODE_R + 6);
                const start = edgeEndpoint(point.x, point.y, CENTER_X, CENTER_Y, CENTER_NODE_R + 4);
                const midX = (start.x + end.x) / 2;
                const midY = (start.y + end.y) / 2;
                return <g key={document.id}>
                  <line x1={start.x} y1={start.y} x2={end.x} y2={end.y} className="graph-edge is-document" markerEnd="url(#graph-arrow)" />
                  <g className="graph-edge-label">
                    <rect x={midX - 36} y={midY - 8} width="72" height="15" rx="7.5" />
                    <text x={midX} y={midY + 3}>mentioned in</text>
                  </g>
                </g>;
              })}

              {neighbors.map((entity) => {
                const point = neighborPositions.get(entity.id)!;
                const list = neighborClaims.get(entity.id) ?? [];
                const inferred = list.every((claim) => claim.kind === 'inferred');
                const end = edgeEndpoint(CENTER_X, CENTER_Y, point.x, point.y, SATELLITE_NODE_R + 6);
                const start = edgeEndpoint(point.x, point.y, CENTER_X, CENTER_Y, CENTER_NODE_R + 4);
                const midX = (start.x + end.x) / 2;
                const midY = (start.y + end.y) / 2;
                const label = humanize(list[0]?.type ?? 'associated with');
                return <g key={entity.id}>
                  <line x1={start.x} y1={start.y} x2={end.x} y2={end.y} className={inferred ? 'graph-edge is-inferred' : 'graph-edge'} markerEnd="url(#graph-arrow)" />
                  <g className="graph-edge-label">
                    <rect x={midX - 52} y={midY - 8} width="104" height="15" rx="7.5" />
                    <text x={midX} y={midY + 3}>{truncate(label, 20)}</text>
                  </g>
                </g>;
              })}

              {center && <g className="graph-center-node">
                <circle cx={CENTER_X} cy={CENTER_Y} r={CENTER_NODE_R + 7} className="graph-center-halo" />
                <circle cx={CENTER_X} cy={CENTER_Y} r={CENTER_NODE_R} className="graph-center-circle" />
                <foreignObject x={CENTER_X - 13} y={CENTER_Y - 26} width="26" height="26">
                  <CenterIcon size={24} strokeWidth={1.6} />
                </foreignObject>
                <text x={CENTER_X} y={CENTER_Y + 16} className="graph-center-name">{truncate(center.label, 16)}</text>
              </g>}

              {linkedDocuments.map((document, index) => {
                const point = docPositions.get(document.id)!;
                return <g key={document.id} className="graph-doc-node" role="button" tabIndex={0} aria-label={`Open document ${document.filename}`}
                  onClick={() => onOpenDocument(document.id)}
                  onKeyDown={(event) => { if (event.key === 'Enter' || event.key === ' ') { event.preventDefault(); onOpenDocument(document.id); } }}>
                  <rect x={point.x - 62} y={point.y - 20} width="124" height="40" rx="8" />
                  <foreignObject x={point.x - 52} y={point.y - 9} width="16" height="18"><FileText size={14} /></foreignObject>
                  <text x={point.x - 32} y={point.y + 1} className="graph-doc-name">{truncate(document.filename, 17)}</text>
                  <text x={point.x - 32} y={point.y + 13} className="graph-doc-sub">source document</text>
                  <title>{document.filename}</title>
                  <circle cx={point.x + 62} cy={point.y - 20} r={2.5} className="graph-node-dot" data-index={index} />
                </g>;
              })}

              {neighbors.map((entity) => {
                const point = neighborPositions.get(entity.id)!;
                const Icon = ENTITY_ICONS[entity.type] ?? Shapes;
                return <g key={entity.id} className="graph-entity-node" role="button" tabIndex={0} aria-label={`Center graph on ${entity.label}`}
                  onClick={() => setSelectedId(entity.id)}
                  onKeyDown={(event) => { if (event.key === 'Enter' || event.key === ' ') { event.preventDefault(); setSelectedId(entity.id); } }}>
                  <circle cx={point.x} cy={point.y} r={SATELLITE_NODE_R} />
                  <foreignObject x={point.x - 8} y={point.y - 14} width="16" height="16"><Icon size={14} /></foreignObject>
                  <text x={point.x} y={point.y + 12} className="graph-entity-type">{truncate(entity.label, 14)}</text>
                  <text x={point.x} y={point.y + 40} className="graph-entity-sub">{humanize(entity.type)}</text>
                  <title>{entity.label} · {humanize(entity.type)}</title>
                </g>;
              })}
            </svg>
          </div>
        </div>

        {center && <aside className="relationship-side" aria-live="polite">
          <div className="relationship-detail relationship-side-card">
            <div className="relationship-detail-heading">
              <div><p>ENTITY DETAILS</p><h3>{center.label}</h3></div>
              <span className={`badge ${center.resolution === 'exact_match' ? 'green' : 'blue'}`}><span className="status-dot" />{center.resolution.replaceAll('_', ' ')}</span>
            </div>
            <div className="relationship-detail-grid">
              <span>Name</span><strong>{center.label}</strong>
              <span>Type</span><strong>{center.type.replaceAll('_', ' ')}</strong>
              <span>Linked Documents</span><strong>{linkedDocuments.length}</strong>
              <span>Relationships</span><strong>{relationshipCount}</strong>
            </div>
            {linkedDocuments.length > 0 && <div className="relationship-side-docs">
              {linkedDocuments.map((document) => <button type="button" key={document.id} className="text-link" onClick={() => onOpenDocument(document.id)}>
                <FileText size={12} /> {truncate(document.filename, 26)} <ArrowUpRight size={12} />
              </button>)}
            </div>}
          </div>

          <div className="relationship-detail relationship-side-card">
            <div className="relationship-detail-heading"><div><p>KEY CONNECTIONS</p><h3>{connections.length} connection{connections.length === 1 ? '' : 's'}</h3></div></div>
            {connections.length === 0 ? <p className="relationship-side-empty">No connections match the current filters.</p> : <ul className="relationship-connection-list">
              {connections.map((connection) => <li key={connection.key}>
                <button type="button" onClick={() => connection.kind === 'entity' ? setSelectedId(connection.targetId) : onOpenDocument(connection.targetId)}>
                  <CheckCircle2 size={13} />
                  <span className="relationship-connection-text">
                    <b>{connection.predicate}</b> {truncate(connection.label, 30)}
                    <small>{connection.claimKind === null ? 'source document'
                      : `${connection.claimKind}${connection.confidence === null ? '' : ` · ${(connection.confidence * 100).toFixed(0)}% uncalibrated`}`}</small>
                  </span>
                  <ArrowUpRight size={13} />
                </button>
              </li>)}
            </ul>}
          </div>
        </aside>}
      </div>

      <div className="relationship-legend">
        <span><i className="legend-entity-circle" /> Extracted entity</span>
        <span><i className="legend-document" /> Source document</span>
        <span><i className="legend-link" /> Explicit claim</span>
        <span><i className="legend-link-inferred" /> Inferred claim</span>
        <span>Model confidence is uncalibrated. A connection is not a verified fact.</span>
      </div>
    </section>
  );
}

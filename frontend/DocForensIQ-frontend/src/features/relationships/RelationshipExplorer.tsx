import { useMemo, useState } from 'react';
import { ArrowUpRight, FileText, GitBranch, Network, Search, ZoomIn, ZoomOut } from 'lucide-react';
import './relationships.css';

export interface RelationshipDocument {
  id: string;
  filename: string;
  content_type: string;
  size_bytes: number;
  processing_status: string;
  created_at: string;
  sha256?: string | null;
}

export interface RelationshipEntity {
  id: string;
  label: string;
  type: string;
  document_ids: string[];
  resolution: string;
}

export interface RelationshipClaim {
  id: string;
  source: string;
  target: string;
  type: string;
  kind: string;
  confidence: number | null;
  source_document_ids: string[];
  source_block_ids: string[];
}

interface Props {
  documents: RelationshipDocument[];
  entities: RelationshipEntity[];
  claims: RelationshipClaim[];
  search: string;
  onSearch: (value: string) => void;
  onOpenDocument: (documentId: string) => void;
  initialTableView?: boolean;
}

type Selection =
  | { kind: 'document'; id: string }
  | { kind: 'entity'; id: string }
  | { kind: 'claim'; id: string }
  | null;

const cardHeight = 68;
const rowGap = 44;
const sidePadding = 54;

function nodeY(index: number, count: number, height: number) {
  return count < 2 ? height / 2 : sidePadding + index * (height - sidePadding * 2) / (count - 1);
}

function shortHash(value?: string | null) {
  if (!value) return 'Unavailable';
  return `${value.slice(0, 12)}…${value.slice(-8)}`;
}

export default function RelationshipExplorer({
  documents,
  entities,
  claims,
  search,
  onSearch,
  onOpenDocument,
  initialTableView = false,
}: Props) {
  const [entityType, setEntityType] = useState('All types');
  const [claimKind, setClaimKind] = useState('All claim types');
  const [confidence, setConfidence] = useState('Any confidence');
  const [selection, setSelection] = useState<Selection>(null);
  const [zoom, setZoom] = useState(100);
  const [tableView, setTableView] = useState(initialTableView);

  const entityTypes = useMemo(() => [...new Set(entities.map((entity) => entity.type))].sort(), [entities]);
  const claimTypes = useMemo(() => [...new Set(claims.map((claim) => claim.type))].sort(), [claims]);
  const needle = search.trim().toLocaleLowerCase();
  const filteredEntities = entities.filter((entity) =>
    (entityType === 'All types' || entity.type === entityType)
    && (!needle || `${entity.label} ${entity.type}`.toLocaleLowerCase().includes(needle)),
  );
  const visibleEntityIds = new Set(filteredEntities.map((entity) => entity.id));
  const entityDocumentIds = new Set(filteredEntities.flatMap((entity) => entity.document_ids));
  const claimDocumentIds = new Set(claims.filter((claim) =>
    [claim.type, claim.kind, claim.source, claim.target].some((value) => value.toLocaleLowerCase().includes(needle))
    || claim.source_block_ids.some((value) => value.toLocaleLowerCase().includes(needle)),
  ).flatMap((claim) => claim.source_document_ids));
  const filteredDocuments = documents.filter((document) =>
    (!needle || document.filename.toLocaleLowerCase().includes(needle)
      || entityDocumentIds.has(document.id) || claimDocumentIds.has(document.id))
    && (entityType === 'All types' || entities.some((entity) =>
      entity.type === entityType && entity.document_ids.includes(document.id))),
  );
  const filteredClaims = claims.filter((claim) => {
    const typeMatches = claimKind === 'All claim types' || claim.type === claimKind;
    const confidenceMatches = confidence === 'Any confidence'
      || (claim.confidence !== null && claim.confidence >= Number(confidence));
    const searchMatches = !needle || [claim.type, claim.kind, claim.source, claim.target]
      .some((value) => value.toLocaleLowerCase().includes(needle))
      || claim.source_block_ids.some((value) => value.toLocaleLowerCase().includes(needle));
    return typeMatches && confidenceMatches && searchMatches
      && visibleEntityIds.has(claim.source) && visibleEntityIds.has(claim.target);
  });

  const documentIndexes = new Map(filteredDocuments.map((document, index) => [document.id, index]));
  const linkedEntities = filteredEntities.filter((entity) =>
    entity.document_ids.some((id) => documentIndexes.has(id)),
  );
  const graphHeight = Math.max(360, Math.max(filteredDocuments.length, linkedEntities.length, 1) * (cardHeight + rowGap));
  const byId = new Map(entities.map((entity) => [entity.id, entity]));
  const selectedDocument = selection?.kind === 'document'
    ? documents.find((document) => document.id === selection.id)
    : undefined;
  const selectedEntity = selection?.kind === 'entity' ? byId.get(selection.id) : undefined;
  const selectedClaim = selection?.kind === 'claim'
    ? claims.find((claim) => claim.id === selection.id)
    : undefined;

  return (
    <section className="panel relationship-explorer" aria-label="Investigation relationship graph">
      <div className="relationship-explorer-heading">
        <div>
          <div className="relationship-explorer-title"><Network size={17} /><h2>Relationship explorer</h2></div>
          <p>Document mentions and model-extracted claims, with source references. Inferred links are candidates for review.</p>
        </div>
        <span className="badge blue">{filteredDocuments.length} documents / {filteredEntities.length} entities</span>
      </div>

      <div className="relationship-toolbar">
        <label className="relationship-search">
          <Search size={15} />
          <span className="sr-only">Search documents, entities, or source blocks</span>
          <input value={search} onChange={(event) => onSearch(event.target.value)} placeholder="Search documents, entities, or source blocks" />
        </label>
        <label className="relationship-filter">Entity type
          <select value={entityType} onChange={(event) => setEntityType(event.target.value)}>
            <option>All types</option>
            {entityTypes.map((type) => <option key={type}>{type}</option>)}
          </select>
        </label>
        <label className="relationship-filter">Relationship
          <select value={claimKind} onChange={(event) => setClaimKind(event.target.value)}>
            <option>All claim types</option>
            {claimTypes.map((type) => <option key={type}>{type}</option>)}
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
          <button type="button" className="button secondary small" aria-pressed={tableView} onClick={() => setTableView((value) => !value)}>{tableView ? 'Graph view' : 'Table view'}</button>
        </div>
      </div>

      {!entities.length ? (
        <div className="relationship-empty">
          <GitBranch size={24} />
          <h3>No extracted connections yet</h3>
          <p>Connections appear after document OCR and relationship extraction complete. Confirm that the OCR worker and relationship extraction are configured, then upload an authorized document.</p>
        </div>
      ) : tableView ? (
        <div className="table-wrap">
          <table className="dashboard-table relationship-table">
            <caption className="sr-only">Entities and their supporting source documents</caption>
            <thead><tr><th>ENTITY</th><th>TYPE</th><th>SOURCE DOCUMENTS</th><th>RESOLUTION</th><th /></tr></thead>
            <tbody>{filteredEntities.map((entity) => <tr key={entity.id}>
              <td><button className="text-link" onClick={() => setSelection({ kind: 'entity', id: entity.id })}>{entity.label}</button></td>
              <td>{entity.type}</td>
              <td>{entity.document_ids.map((id) => documents.find((document) => document.id === id)?.filename ?? id).join(', ') || 'No source documents'}</td>
              <td>{entity.resolution}</td>
              <td><button className="icon-button" aria-label={`Review ${entity.label}`} onClick={() => setSelection({ kind: 'entity', id: entity.id })}><ArrowUpRight size={15} /></button></td>
            </tr>)}</tbody>
          </table>
        </div>
      ) : (
        <div className="relationship-canvas-scroll" tabIndex={0} aria-label="Relationship graph. Use Tab to select a document or entity.">
          <div className="relationship-canvas-zoom" style={{ height: graphHeight * zoom / 100, minHeight: graphHeight * zoom / 100 }}>
            <div className="relationship-canvas" style={{
              height: graphHeight,
              minHeight: graphHeight,
              transform: `scale(${zoom / 100})`,
              transformOrigin: 'top left',
              width: '100%',
            }}>
            <div className="relationship-column-label relationship-documents-label"><FileText size={14} /> Source documents</div>
            <div className="relationship-column-label relationship-entities-label"><Network size={14} /> Extracted entities</div>
            <svg className="relationship-connectors" viewBox={`0 0 1000 ${graphHeight}`} preserveAspectRatio="none" aria-hidden="true">
              <defs><marker id="relationship-arrow" markerWidth="8" markerHeight="8" refX="7" refY="4" orient="auto"><path d="M0,0 L8,4 L0,8 z" /></marker></defs>
              {linkedEntities.flatMap((entity, entityIndex) => entity.document_ids.flatMap((documentId) => {
                const documentIndex = documentIndexes.get(documentId);
                if (documentIndex === undefined) return [];
                const y1 = nodeY(documentIndex, filteredDocuments.length, graphHeight);
                const y2 = nodeY(entityIndex, linkedEntities.length, graphHeight);
                const highlighted = selection?.kind === 'entity' ? selection.id === entity.id
                  : selection?.kind === 'document' ? selection.id === documentId
                    : true;
                return [<path
                  key={`${documentId}-${entity.id}`}
                  d={`M 300 ${y1} C 420 ${y1}, 580 ${y2}, 700 ${y2}`}
                  className={highlighted ? 'relationship-connector is-highlighted' : 'relationship-connector'}
                  markerEnd="url(#relationship-arrow)"
                />];
              }))}
            </svg>
            <div className="relationship-node-column relationship-documents">
              {filteredDocuments.map((document, index) => {
                const active = selection?.kind === 'document' && selection.id === document.id;
                return <button
                  type="button"
                  className={`relationship-node document-node ${active ? 'is-selected' : ''}`}
                  style={{ top: nodeY(index, filteredDocuments.length, graphHeight) - cardHeight / 2 }}
                  key={document.id}
                  aria-pressed={active}
                  onClick={() => setSelection({ kind: 'document', id: document.id })}
                >
                  <FileText size={17} />
                  <span><strong>{document.filename}</strong><small>{document.processing_status.replaceAll('_', ' ')}</small></span>
                  <ArrowUpRight size={14} />
                </button>;
              })}
              </div>
            </div>
            <div className="relationship-node-column relationship-entities">
              {linkedEntities.map((entity, index) => {
                const active = selection?.kind === 'entity' && selection.id === entity.id;
                return <button
                  type="button"
                  className={`relationship-node entity-node ${active ? 'is-selected' : ''}`}
                  style={{ top: nodeY(index, linkedEntities.length, graphHeight) - cardHeight / 2 }}
                  key={entity.id}
                  aria-pressed={active}
                  onClick={() => setSelection({ kind: 'entity', id: entity.id })}
                >
                  <Network size={17} />
                  <span><strong>{entity.label}</strong><small>{entity.type} / {entity.resolution}</small></span>
                  <ArrowUpRight size={14} />
                </button>;
              })}
            </div>
          </div>
        </div>
      )}

      <div className="relationship-legend">
        <span><i className="legend-document" /> Source document</span>
        <span><i className="legend-entity" /> Extracted entity</span>
        <span><i className="legend-link" /> Entity mention supported by OCR</span>
        <span>Model confidence is uncalibrated. A connection is not a verified fact.</span>
      </div>

      {filteredClaims.length > 0 && <div className="relationship-claims">
        <h3>Relationship claims</h3>
        <div className="relationship-claim-list">
          {filteredClaims.map((claim) => <button
            type="button"
            className={`relationship-claim ${selection?.kind === 'claim' && selection.id === claim.id ? 'is-selected' : ''}`}
            key={claim.id}
            onClick={() => setSelection({ kind: 'claim', id: claim.id })}
          >
            <span className="relationship-claim-endpoint">{byId.get(claim.source)?.label ?? claim.source}</span>
            <span className={`relationship-claim-kind ${claim.kind === 'inferred' ? 'is-inferred' : ''}`}>{claim.type.replaceAll('_', ' ')} <ArrowRightIcon /></span>
            <span className="relationship-claim-endpoint">{byId.get(claim.target)?.label ?? claim.target}</span>
            <small>{claim.kind}{claim.confidence === null ? '' : ` / ${(claim.confidence * 100).toFixed(0)}% uncalibrated`}</small>
          </button>)}
        </div>
      </div>}

      {(selectedDocument || selectedEntity || selectedClaim) && <aside className="relationship-detail" aria-live="polite" aria-label="Selected graph item details">
        <div className="relationship-detail-heading">
          <div><p>{selectedDocument ? 'SOURCE DOCUMENT' : selectedEntity ? 'EXTRACTED ENTITY' : 'RELATIONSHIP CLAIM'}</p>
            <h3>{selectedDocument?.filename ?? selectedEntity?.label ?? `${byId.get(selectedClaim?.source ?? '')?.label ?? 'Entity'} to ${byId.get(selectedClaim?.target ?? '')?.label ?? 'entity'}`}</h3>
          </div>
          <button type="button" className="button secondary small" onClick={() => setSelection(null)}>Clear selection</button>
        </div>
        {selectedDocument && <div className="relationship-detail-grid">
          <span>Processing status</span><strong>{selectedDocument.processing_status.replaceAll('_', ' ')}</strong>
          <span>SHA-256</span><code>{shortHash(selectedDocument.sha256)}</code>
          <span>Size</span><strong>{(selectedDocument.size_bytes / 1024 / 1024).toFixed(2)} MiB</strong>
          <button type="button" className="text-link" onClick={() => onOpenDocument(selectedDocument.id)}>Open document review <ArrowUpRight size={14} /></button>
        </div>}
        {selectedEntity && <div className="relationship-detail-grid">
          <span>Entity type</span><strong>{selectedEntity.type}</strong>
          <span>Resolution</span><strong>{selectedEntity.resolution}</strong>
          <span>Source documents</span><strong>{selectedEntity.document_ids.map((id) => documents.find((document) => document.id === id)?.filename ?? id).join(', ') || 'No document references'}</strong>
        </div>}
        {selectedClaim && <div className="relationship-detail-grid">
          <span>Relationship</span><strong>{selectedClaim.type.replaceAll('_', ' ')}</strong>
          <span>Basis</span><strong>{selectedClaim.kind === 'explicit' ? 'Explicitly stated in source blocks' : 'Inferred by the extraction model; requires investigator review'}</strong>
          <span>Model confidence</span><strong>{selectedClaim.confidence === null ? 'Not provided' : `${(selectedClaim.confidence * 100).toFixed(0)}% (uncalibrated)`}</strong>
          <span>Source blocks</span><code>{selectedClaim.source_block_ids.join(', ')}</code>
          <span>Source documents</span><strong>{selectedClaim.source_document_ids.map((id) => documents.find((document) => document.id === id)?.filename ?? id).join(', ')}</strong>
        </div>}
      </aside>}
    </section>
  );
}

function ArrowRightIcon() {
  return <ArrowUpRight size={13} aria-hidden="true" />;
}

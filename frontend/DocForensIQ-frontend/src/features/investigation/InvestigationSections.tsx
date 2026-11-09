import { useMemo } from 'react';
import { useQueries, useQuery } from '@tanstack/react-query';
import { Activity, CalendarClock, CheckCircle2, FileCheck2, FileText, Layers3, Link2, ShieldAlert } from 'lucide-react';
import { Link } from 'react-router-dom';
import { apiRequest } from '../../services/apiClient';
import type { RelationshipDocument } from '../relationships/RelationshipExplorer';

interface InvestigationRecord {
  id: string; case_number: string; title: string; description?: string | null; status: string;
  document_count: number; created_at: string; updated_at: string;
}
interface DocumentRecord extends RelationshipDocument { source_description?: string | null; }
interface OCRPayload {
  document_id: string; provider: string; provider_version: string | null; recognized_text: string;
  blocks: Array<{ block_id: string; source_path: string; text: string; page_index?: number; engine_bbox?: unknown; engine_confidence?: number }>;
}
interface TimelineRecord { id: string; raw_date: string; interpreted_date: string | null; ambiguous: boolean; description: string; confidence: number; evidence_refs: Array<{ document_id?: string; filename?: string; page_number?: number | null; block_id?: string | null }>; annotation: string | null; }
interface FindingRecord { id: string; title: string; severity: string; status: string; finding_type: string; }

const processedStatuses = new Set(['ocr_completed', 'relationship_extraction_pending', 'relationship_extraction_completed', 'relationship_extraction_failed', 'completed']);
const dateLabel = (value: string) => new Intl.DateTimeFormat(undefined, { dateStyle: 'medium', timeStyle: 'short' }).format(new Date(value));
const formatBytes = (value: number) => value < 1024 * 1024 ? `${(value / 1024).toFixed(0)} KB` : `${(value / 1024 / 1024).toFixed(2)} MB`;
const typeLabel = (document: DocumentRecord) => document.content_type || 'Unknown file type';

export function InvestigationOverview({ investigationId }: { investigationId: string }) {
  const investigation = useQuery({ queryKey: ['investigation', investigationId], queryFn: () => apiRequest<InvestigationRecord>(`/investigations/${investigationId}`) });
  const documents = useQuery({ queryKey: ['documents', investigationId], queryFn: () => apiRequest<DocumentRecord[]>(`/investigations/${investigationId}/documents`) });
  const findings = useQuery({ queryKey: ['findings', investigationId, ''], queryFn: () => apiRequest<{ items: FindingRecord[] }>(`/investigations/${investigationId}/findings`) });
  const timeline = useQuery({ queryKey: ['timeline', investigationId], queryFn: () => apiRequest<{ items: TimelineRecord[] }>(`/investigations/${investigationId}/timeline`) });
  const correlations = useQuery({ queryKey: ['correlation', investigationId], queryFn: () => apiRequest<{ summary: { total_links: number; confirmed_links: number; candidate_links: number; contradictions: number } }>(`/investigations/${investigationId}/correlation`) });
  const docs = documents.data ?? [];
  const items = findings.data?.items ?? [];
  const events = useMemo(() => [...(timeline.data?.items ?? [])].sort((a, b) => {
    const left = a.interpreted_date ? Date.parse(a.interpreted_date) : Number.NaN;
    const right = b.interpreted_date ? Date.parse(b.interpreted_date) : Number.NaN;
    if (Number.isFinite(left) && Number.isFinite(right)) return left - right;
    if (Number.isFinite(left)) return -1;
    if (Number.isFinite(right)) return 1;
    return a.raw_date.localeCompare(b.raw_date);
  }), [timeline.data]);
  const pendingDocs = docs.filter((doc) => ['queued', 'processing', 'relationship_extraction_pending'].includes(doc.processing_status)).length;
  const completedDocs = docs.filter((doc) => processedStatuses.has(doc.processing_status)).length;
  const workflowSteps: Array<[string, string, string, boolean]> = [
    ['01', 'Documents collected', `${docs.length} source document(s)`, docs.length > 0],
    ['02', 'OCR and extraction', `${completedDocs} ready${pendingDocs ? ` · ${pendingDocs} processing` : ''}`, completedDocs > 0],
    ['03', 'Cross-document review', `${correlations.data?.summary.total_links ?? 0} links · ${correlations.data?.summary.contradictions ?? 0} comparisons flagged`, Boolean(correlations.data?.summary.total_links)],
    ['04', 'Findings review', `${items.length} candidate finding(s)`, items.length > 0],
    ['05', 'Timeline and reporting', `${events.length} dated event(s)`, events.length > 0],
  ];
  if (investigation.isLoading || documents.isLoading) return <div className="panel" role="status">Loading investigation overview…</div>;
  if (investigation.isError || documents.isError) return <div className="panel" role="alert">Investigation overview could not be loaded. Refresh this page and try again.</div>;

  return <div className="investigation-section-page">
    <section className="investigation-overview-hero"><div><p className="eyebrow"><Layers3 size={14}/> INVESTIGATION OVERVIEW · {investigation.data?.case_number}</p><h2>{investigation.data?.title}</h2><p>{investigation.data?.description || 'No investigation scope description has been recorded.'}</p></div><span className={`badge ${investigation.data?.status === 'completed' ? 'green' : 'blue'}`}>{investigation.data?.status?.replaceAll('_', ' ')}</span></section>
    <div className="stat-grid investigation-overview-stats">
      <article className="stat-card"><FileText size={17}/><span className="stat-label">SOURCE DOCUMENTS</span><strong>{docs.length}</strong><small>{completedDocs} OCR-ready · {pendingDocs} processing</small></article>
      <article className="stat-card"><FileCheck2 size={17}/><span className="stat-label">CANDIDATE FINDINGS</span><strong>{findings.isLoading ? '—' : items.length}</strong><small>{items.filter((item) => item.status === 'accepted').length} accepted by investigator · automated results remain candidates</small></article>
      <article className="stat-card"><Link2 size={17}/><span className="stat-label">CROSS-DOCUMENT LINKS</span><strong>{correlations.data?.summary.total_links ?? (correlations.isLoading ? '—' : 0)}</strong><small>{correlations.data?.summary.confirmed_links ?? 0} confirmed · {correlations.data?.summary.candidate_links ?? 0} candidates</small></article>
      <article className="stat-card"><CalendarClock size={17}/><span className="stat-label">TIMELINE EVENTS</span><strong>{timeline.data?.items.length ?? (timeline.isLoading ? '—' : 0)}</strong><small>Events retain the date interpretation and source reference</small></article>
    </div>
    <div className="dashboard-columns investigation-overview-columns">
      <section className="panel"><div className="panel-head"><div><h3>Investigation workflow</h3><p>Current coverage from persisted records</p></div><Activity size={16}/></div><ol className="investigation-workflow-list">
        {workflowSteps.map(([step, label, detail, complete]) => <li key={step}><span className={`workflow-step-mark ${complete ? 'complete' : ''}`}>{complete ? <CheckCircle2 size={15}/> : step}</span><div><b>{label}</b><small>{detail}</small></div></li>)}
      </ol></section>
      <section className="panel"><div className="panel-head"><div><h3>Evidence inventory</h3><p>Stored sources · latest activity {investigation.data?.updated_at ? dateLabel(investigation.data.updated_at) : 'unavailable'}</p></div><Link className="text-link" to={`/investigations/${investigationId}/classification`}>Review classification</Link></div>{docs.length ? <ul className="overview-document-list">{docs.slice(0, 6).map((doc) => <li key={doc.id}><span className="overview-file-icon"><FileText size={15}/></span><span className="overview-document-copy"><b>{doc.filename}</b><small>{typeLabel(doc)} · {formatBytes(doc.size_bytes)}</small></span><span className="badge blue">{doc.processing_status.replaceAll('_', ' ')}</span><Link className="text-link" aria-label={`Open ${doc.filename}`} to={`/investigations/${investigationId}?doc=${doc.id}`}>Open</Link></li>)}</ul> : <div className="dashboard-empty"><p>No source documents have been added.</p><Link className="button secondary small" to={`/investigations/${investigationId}`}>Add evidence</Link></div>}</section>
    </div>
    <section className="panel"><div className="panel-head"><div><h3>Latest timeline events</h3><p>Chronologically ordered where the OCR date can be interpreted.</p></div><Link className="text-link" to={`/investigations/${investigationId}/timeline`}>Open timeline</Link></div>{timeline.isLoading ? <p role="status">Loading stored events…</p> : events.length ? <div className="overview-timeline">{events.slice(0, 4).map((event) => <article key={event.id}><span className="overview-timeline-dot"/><div><b>{event.interpreted_date || event.raw_date}{event.ambiguous ? ' · ambiguous' : ''}</b><p>{event.description}</p><small>{event.evidence_refs[0]?.filename || 'Source not named'}{event.evidence_refs[0]?.page_number ? ` · page ${event.evidence_refs[0].page_number}` : ''}</small></div></article>)}</div> : <p className="panel-foot">No timeline events have been generated yet.</p>}</section>
    <p className="investigation-data-note"><ShieldAlert size={14}/> This summary reflects persisted analysis records. Missing data is shown as unavailable or zero; it is not inferred.</p>
  </div>;
}

export function InvestigationClassification({ investigationId, documents }: { investigationId: string; documents: DocumentRecord[] }) {
  const counts = useMemo(() => documents.reduce((map, doc) => map.set(typeLabel(doc), (map.get(typeLabel(doc)) ?? 0) + 1), new Map<string, number>()), [documents]);
  const statusCounts = useMemo(() => documents.reduce((map, doc) => map.set(doc.processing_status, (map.get(doc.processing_status) ?? 0) + 1), new Map<string, number>()), [documents]);
  return <div className="investigation-section-page">
    <section className="panel section-intro"><p className="eyebrow"><FileCheck2 size={14}/> DOCUMENT CLASSIFICATION</p><h2>Classification and processing coverage</h2><p>Stored file formats and OCR workflow states are reported from the investigation records. This API does not persist a semantic document-type prediction, so labels such as invoice, payslip, or bank statement are not inferred here.</p><div className="classification-summary"><div><b>{documents.length}</b><small>documents in scope</small></div><div><b>{counts.size}</b><small>stored media types</small></div><div><b>{statusCounts.size}</b><small>processing states</small></div></div></section>
    <section className="panel"><div className="panel-head"><div><h3>Source format breakdown</h3><p>Based on stored content type</p></div></div><div className="classification-type-grid">{[...counts.entries()].map(([type, count]) => <article key={type}><span className="overview-file-icon"><FileText size={17}/></span><b>{type}</b><strong>{count}</strong><small>{count === 1 ? 'document' : 'documents'}</small></article>)}</div></section>
    <section className="panel"><div className="panel-head"><div><h3>Document-by-document status</h3><p>Format, provenance, processing outcome, and source access</p></div></div>{documents.length ? <div className="table-wrap"><table className="dashboard-table"><thead><tr><th>DOCUMENT</th><th>STORED FORMAT</th><th>OCR / PROCESSING</th><th>SIZE</th><th>SHA-256</th><th/></tr></thead><tbody>{documents.map((doc) => <tr key={doc.id}><td><b>{doc.filename}</b><small className="classification-provenance">{doc.source_description || 'Provenance note not recorded'}</small></td><td>{typeLabel(doc)}</td><td><span className={`badge ${doc.processing_status.includes('failed') ? 'red' : processedStatuses.has(doc.processing_status) ? 'green' : 'amber'}`}>{doc.processing_status.replaceAll('_', ' ')}</span></td><td>{formatBytes(doc.size_bytes)}</td><td><code>{doc.sha256 ? `${doc.sha256.slice(0, 14)}…` : 'Unavailable'}</code></td><td><Link className="text-link" to={`/investigations/${investigationId}?doc=${doc.id}`}>Inspect</Link></td></tr>)}</tbody></table></div> : <div className="dashboard-empty"><p>No source documents to classify.</p><Link className="button secondary small" to={`/investigations/${investigationId}`}>Add evidence</Link></div>}</section>
    <p className="investigation-data-note"><ShieldAlert size={14}/> Semantic document classification is unavailable in the persisted API response. File format does not establish the document's business purpose.</p>
  </div>;
}

export function InvestigationExtractedData({ investigationId, documents }: { investigationId: string; documents: DocumentRecord[] }) {
  const results = useQueries({ queries: documents.filter((doc) => processedStatuses.has(doc.processing_status)).map((doc) => ({
    queryKey: ['ocr', investigationId, doc.id, false],
    queryFn: () => apiRequest<OCRPayload>(`/investigations/${investigationId}/documents/${doc.id}/ocr`),
  })) });
  const readyDocs = documents.filter((doc) => processedStatuses.has(doc.processing_status));
  const isLoading = results.some((result) => result.isLoading);
  return <div className="investigation-section-page">
    <section className="panel section-intro"><p className="eyebrow"><FileText size={14}/> OCR-EXTRACTED DATA</p><h2>Recognized information by source</h2><p>Text below is taken from persisted OCR blocks, grouped by source document and page. Explicit label/value lines are formatted for scanning while preserving the original text and block citation.</p><div className="classification-summary"><div><b>{readyDocs.length}</b><small>OCR-ready documents</small></div><div><b>{results.reduce((sum, result) => sum + (result.data?.blocks.length ?? 0), 0)}</b><small>stored OCR blocks loaded</small></div><div><b>{results.reduce((sum, result) => sum + (result.data?.blocks.reduce((count, block) => count + (block.engine_confidence != null ? 1 : 0), 0) ?? 0), 0)}</b><small>blocks with engine score</small></div></div></section>
    {isLoading && <div className="panel" role="status">Loading OCR data for stored sources…</div>}
    {!readyDocs.length && <div className="panel dashboard-empty"><FileText size={20}/><h3>No OCR results available</h3><p>Documents will appear here after OCR processing completes.</p><Link className="text-link" to={`/investigations/${investigationId}/classification`}>Review processing status</Link></div>}
    {readyDocs.map((doc, index) => {
      const query = results[index];
      const blocks = query?.data?.blocks ?? [];
      const pages = new Map<number, typeof blocks>();
      for (const block of blocks) { const page = (block.page_index ?? 0) + 1; pages.set(page, [...(pages.get(page) ?? []), block]); }
      return <section className="panel extracted-document" key={doc.id}><div className="panel-head"><div><p className="eyebrow">SOURCE DOCUMENT</p><h3>{doc.filename}</h3><p>{query?.data?.provider || 'OCR provider unavailable'}{query?.data?.provider_version ? ` · ${query.data.provider_version}` : ''} · {blocks.length} block(s)</p></div><Link className="button secondary small" to={`/investigations/${investigationId}?doc=${doc.id}`}>Open source</Link></div>
        {query?.isError ? <div className="panel-foot" role="alert">OCR data could not be loaded for this source. <Link className="text-link" to={`/investigations/${investigationId}?doc=${doc.id}`}>Open document</Link></div> : blocks.length ? [...pages.entries()].sort(([a], [b]) => a - b).map(([page, pageBlocks]) => <div className="extracted-page" key={page}><div className="extracted-page-heading"><span>PAGE {page}</span><span>{pageBlocks.length} extracted region(s)</span></div><div className="extracted-field-grid">{pageBlocks.map((block) => {
          const match = block.text.match(/^\s*([^:|–—]{2,60})\s*[:|–—]\s*(.+?)\s*$/);
          return <article className="extracted-field-card" key={block.block_id}><div className="extracted-field-heading"><b>{match?.[1]?.trim() || 'Recognized text'}</b><Link className="extracted-block-link" to={`/investigations/${investigationId}?doc=${doc.id}&page=${page}&block=${encodeURIComponent(block.block_id)}`}>{block.block_id}</Link></div><p>{match?.[2]?.trim() || block.text || 'Empty OCR block'}</p><small>{block.engine_confidence == null ? 'OCR score unavailable' : `Engine score ${(block.engine_confidence * 100).toFixed(0)}% · not calibrated accuracy`}{block.source_path ? ` · ${block.source_path}` : ''}</small><details><summary>Original OCR block</summary><blockquote>{block.text}</blockquote></details></article>;
        })}</div></div>) : <p className="panel-foot">No non-empty OCR blocks are stored for this source.</p>}
      </section>;
    })}
  </div>;
}

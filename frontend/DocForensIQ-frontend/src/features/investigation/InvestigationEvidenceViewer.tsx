import { useEffect, useMemo, useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { ChevronLeft, ChevronRight, FileImage, Search, ShieldAlert } from 'lucide-react';
import { Link, useSearchParams } from 'react-router-dom';
import { apiBlob, apiRequest } from '../../services/apiClient';
import type { RelationshipDocument } from '../relationships/RelationshipExplorer';

interface OCRPayload { document_id: string; provider: string; provider_version: string | null; blocks: Array<{ block_id: string; source_path: string; text: string; page_index?: number; engine_bbox?: unknown; engine_confidence?: number }>; }
interface ExtractionRuns { items: Array<{ id: string; run_number: number; status: string }> }
interface ExtractionDetail { run_number: number; pages: Array<{ page_number: number; width: number | null; height: number | null }> }
interface VisualRun { id: string; document_id: string; status: string; created_at: string; result: { page_count?: number; observations?: Array<{ finding_id: string; page_number: number; check_type: string; explanation: string; metrics: Record<string, unknown>; regions: Array<{ x0: number; y0: number; x1: number; y1: number }>; evidence_ref: { block_id?: string | null } }> } }
interface FindingEvidence { document_id?: string; filename?: string; page_number?: number | null; block_id?: string | null; quoted_text?: string; field_type?: string; bounding_box?: { x0?: number; y0?: number; x1?: number; y1?: number } | null; }
interface InvestigationFinding { id: string; title: string; description: string; severity: string; status: string; evidence_refs: FindingEvidence[]; }

const readyStatuses = new Set(['ocr_completed', 'relationship_extraction_pending', 'relationship_extraction_completed', 'relationship_extraction_failed', 'completed']);
function bounds(value: unknown) {
  if (!Array.isArray(value)) return null;
  const points: number[][] | null = value.length === 4 && value.every((part) => typeof part === 'number')
    ? [[value[0] as number, value[1] as number], [value[2] as number, value[3] as number]]
    : value.length >= 4 && value.every((point) => Array.isArray(point) && point.length >= 2 && point.every((part) => typeof part === 'number')) ? value as number[][] : null;
  if (!points) return null;
  const xs = points.map((point) => point[0]), ys = points.map((point) => point[1]);
  const left = Math.min(...xs), top = Math.min(...ys), right = Math.max(...xs), bottom = Math.max(...ys);
  return right > left && bottom > top ? { left, top, width: right - left, height: bottom - top } : null;
}

export default function InvestigationEvidenceViewer({ investigationId, documents }: { investigationId: string; documents: RelationshipDocument[] }) {
  const [params, setParams] = useSearchParams();
  const [search, setSearch] = useState('');
  const [imageSize, setImageSize] = useState({ width: 0, height: 0 });
  const requestedDocument = params.get('doc') || '';
  const selectedId = documents.some((doc) => doc.id === requestedDocument) ? requestedDocument : documents[0]?.id || '';
  const document = documents.find((doc) => doc.id === selectedId);
  const page = Math.max(1, Number(params.get('page')) || 1);
  const [highlightedBlock, setHighlightedBlock] = useState(params.get('block') || '');
  useEffect(() => { if (requestedDocument !== selectedId && selectedId) { setParams({ doc: selectedId }); setHighlightedBlock(''); } }, [requestedDocument, selectedId, setParams]);
  useEffect(() => { setHighlightedBlock(params.get('block') || ''); }, [params]);
  useEffect(() => { setImageSize({ width: 0, height: 0 }); }, [selectedId, page]);
  const ocr = useQuery({ queryKey: ['ocr', investigationId, selectedId, false], queryFn: () => apiRequest<OCRPayload>(`/investigations/${investigationId}/documents/${selectedId}/ocr`), enabled: Boolean(document && readyStatuses.has(document.processing_status)) });
  const file = useQuery({ queryKey: ['evidence-file', investigationId, selectedId], queryFn: () => apiBlob(`/investigations/${investigationId}/documents/${selectedId}/file`), enabled: Boolean(document && document.content_type !== 'application/pdf'), retry: false });
  const preview = useQuery({ queryKey: ['evidence-page', investigationId, selectedId, page], queryFn: () => apiBlob(`/investigations/${investigationId}/documents/${selectedId}/pages/${page}/preview`), enabled: Boolean(document?.content_type === 'application/pdf'), retry: false });
  const runs = useQuery({ queryKey: ['extraction-runs', investigationId, selectedId], queryFn: () => apiRequest<ExtractionRuns>(`/investigations/${investigationId}/documents/${selectedId}/extraction-runs`), enabled: Boolean(document) });
  const extraction = useQuery({ queryKey: ['extraction-run', investigationId, selectedId, runs.data?.items[0]?.id], queryFn: () => apiRequest<ExtractionDetail>(`/investigations/${investigationId}/documents/${selectedId}/extraction-runs/${runs.data!.items[0].id}`), enabled: Boolean(runs.data?.items[0]?.id) });
  const visualRuns = useQuery({ queryKey: ['visual-runs', investigationId], queryFn: () => apiRequest<{ items: VisualRun[] }>(`/investigations/${investigationId}/visual-analysis-runs`), enabled: Boolean(document) });
  const findings = useQuery({ queryKey: ['findings', investigationId, ''], queryFn: () => apiRequest<{ items: InvestigationFinding[] }>(`/investigations/${investigationId}/findings`), enabled: Boolean(document) });
  const sourceBlob = document?.content_type === 'application/pdf' ? preview.data : file.data;
  const sourceUrl = useMemo(() => sourceBlob && typeof URL.createObjectURL === 'function' ? URL.createObjectURL(sourceBlob) : '', [sourceBlob]);
  useEffect(() => () => { if (sourceUrl) URL.revokeObjectURL(sourceUrl); }, [sourceUrl]);
  const blocks = (ocr.data?.blocks ?? []).filter((block) => (block.page_index ?? 0) === page - 1 && (!search.trim() || `${block.block_id} ${block.text}`.toLocaleLowerCase().includes(search.trim().toLocaleLowerCase())));
  const currentRun = (visualRuns.data?.items ?? []).filter((run) => run.document_id === selectedId && run.status !== 'failed').sort((a, b) => b.created_at.localeCompare(a.created_at))[0];
  const observations = (currentRun?.result.observations ?? []).filter((item) => item.page_number === page);
  const citedFindings = (findings.data?.items ?? []).flatMap((finding) => finding.evidence_refs.filter((ref) => ref.document_id === selectedId && (ref.page_number == null || ref.page_number === page)).map((ref) => ({ finding, ref })));
  const pageCount = Math.max(1, extraction.data?.pages.length ?? currentRun?.result.page_count ?? 1);
  const pageDimensions = extraction.data?.pages.find((item) => item.page_number === page);
  const isPdf = document?.content_type === 'application/pdf';
  const ratioX = isPdf && pageDimensions?.width && imageSize.width ? imageSize.width / pageDimensions.width : 1;
  const ratioY = isPdf && pageDimensions?.height && imageSize.height ? imageSize.height / pageDimensions.height : 1;

  if (!documents.length) return <div className="panel dashboard-empty"><FileImage size={21}/><h3>No evidence to view</h3><p>Upload documents to this investigation to open source files and OCR evidence.</p><Link className="button secondary small" to={`/investigations/${investigationId}`}>Go to documents</Link></div>;
  return <div className="evidence-viewer-page">
    <section className="panel evidence-viewer-intro"><div><p className="eyebrow"><FileImage size={14}/> SOURCE-ANCHORED REVIEW</p><h2>Evidence viewer</h2><p>Inspect the stored source alongside its OCR blocks and saved visual observations. Select a block to highlight its source region when coordinates are available.</p></div><label className="auth-label">Source document<select aria-label="Evidence source document" value={selectedId} onChange={(event) => { setParams({ doc: event.target.value }); setHighlightedBlock(''); }}>
      {documents.map((item) => <option key={item.id} value={item.id}>{item.filename}</option>)}
    </select></label></section>
    {document && <div className="evidence-viewer-grid">
      <section className="panel evidence-source-panel"><div className="panel-head"><div><h3>{document.filename}</h3><p>{document.content_type} · {document.sha256 ? `SHA-256 ${document.sha256.slice(0, 16)}…` : 'Source hash unavailable'}</p></div><span className="badge blue">{document.processing_status.replaceAll('_', ' ')}</span></div>
        <div className="evidence-source-controls"><button type="button" className="button secondary small" aria-label="Previous source page" disabled={page <= 1} onClick={() => setParams({ doc: selectedId, page: String(Math.max(1, page - 1)) })}><ChevronLeft size={14}/>Previous</button><span>Page {page} of {pageCount}</span><button type="button" className="button secondary small" aria-label="Next source page" disabled={page >= pageCount} onClick={() => setParams({ doc: selectedId, page: String(Math.min(pageCount, page + 1)) })}>Next<ChevronRight size={14}/></button></div>
        <div className="evidence-viewer-canvas">{(!isPdf && file.isLoading) || (isPdf && preview.isLoading) ? <p role="status">Loading stored source evidence…</p> : (!isPdf && file.isError) || (isPdf && preview.isError) ? <p role="alert">Source evidence could not be opened. The stored OCR text is still shown where available.</p> : sourceUrl ? <div className="evidence-source-image-wrap"><img src={sourceUrl} alt={`${document.filename}, page ${page}`} onLoad={(event) => setImageSize({ width: event.currentTarget.naturalWidth, height: event.currentTarget.naturalHeight })}/>
          {blocks.map((block) => { const box = bounds(block.engine_bbox); if (!box || !imageSize.width || !imageSize.height) return null; return <button type="button" key={block.block_id} aria-label={`Select OCR block ${block.block_id}`} title={`${block.block_id}: ${block.text}`} className={`evidence-block-overlay ${highlightedBlock === block.block_id ? 'is-active' : ''}`} style={{ left: `${box.left * ratioX / imageSize.width * 100}%`, top: `${box.top * ratioY / imageSize.height * 100}%`, width: `${box.width * ratioX / imageSize.width * 100}%`, height: `${box.height * ratioY / imageSize.height * 100}%` }} onClick={() => setHighlightedBlock(block.block_id)}/>; })}
          {observations.flatMap((observation) => observation.regions.map((region, index) => <span aria-hidden="true" key={`${observation.finding_id}-${index}`} className="evidence-anomaly-overlay" style={{ left: `${region.x0 * (isPdf ? 1.5 : 1) / imageSize.width * 100}%`, top: `${region.y0 * (isPdf ? 1.5 : 1) / imageSize.height * 100}%`, width: `${(region.x1 - region.x0) * (isPdf ? 1.5 : 1) / imageSize.width * 100}%`, height: `${(region.y1 - region.y0) * (isPdf ? 1.5 : 1) / imageSize.height * 100}%` }} />))}
        </div> : <p>Preview unavailable.</p>}</div>
        <div className="evidence-viewer-key"><span><i className="ocr-key"/> OCR block regions</span><span><i className="anomaly-key"/> Saved visual observations</span></div>
      </section>
      <section className="panel evidence-text-panel"><div className="panel-head"><div><h3>OCR evidence</h3><p>{ocr.data?.provider || (ocr.isLoading ? 'Loading stored OCR…' : 'No OCR provider recorded')} · {blocks.length} visible block(s)</p></div></div>
        <label className="evidence-block-search"><Search size={14}/><input value={search} onChange={(event) => setSearch(event.target.value)} placeholder="Filter source text or block ID" aria-label="Filter OCR evidence"/></label>
        {ocr.isError ? <div role="alert">Stored OCR could not be loaded for this document.</div> : !readyStatuses.has(document.processing_status) ? <p className="panel-foot">OCR is not complete for this source yet.</p> : ocr.isLoading ? <p role="status">Loading extracted evidence…</p> : blocks.length ? <div className="evidence-block-list">{blocks.map((block) => <button id={`evidence-block-${block.block_id}`} type="button" key={block.block_id} className={`evidence-block-card ${highlightedBlock === block.block_id ? 'is-active' : ''}`} onClick={() => { setHighlightedBlock(block.block_id); setParams({ doc: selectedId, page: String(page), block: block.block_id }); }}><span className="evidence-block-card-head"><b>{block.block_id}</b><small>Page {page} · {block.source_path}</small></span><span className="evidence-block-text">{block.text || 'Empty OCR block'}</span><small>{block.engine_confidence == null ? 'Confidence unavailable' : `OCR engine score ${(block.engine_confidence * 100).toFixed(0)}% · not calibrated accuracy`}{block.engine_bbox ? ` · coordinates ${JSON.stringify(block.engine_bbox)}` : ''}</small></button>)}</div> : <p className="panel-foot">No OCR blocks match this page and filter.</p>}
      </section>
    </div>}
    {document && <section className="panel evidence-visual-findings"><div className="panel-head"><div><h3>Investigation findings citing this page</h3><p>Candidate findings and source quotations recorded in this investigation.</p></div><Link className="text-link" to={`/investigations/${investigationId}/findings`}>Review findings</Link></div>{findings.isLoading ? <p role="status">Loading finding references…</p> : findings.isError ? <p role="alert">Finding references could not be loaded.</p> : citedFindings.length ? <div className="evidence-observation-list">{citedFindings.map(({ finding, ref }, index) => <article key={`${finding.id}-${index}`}><div><b>{finding.title}</b><span className={`badge ${finding.status === 'accepted' ? 'green' : 'amber'}`}>{finding.status.replaceAll('_', ' ')} · {finding.severity}</span></div><p>{ref.quoted_text || finding.description}</p><small>{ref.filename || document.filename}{ref.page_number == null ? '' : ` · page ${ref.page_number}`}{ref.block_id ? ` · ${ref.block_id}` : ''}{ref.field_type ? ` · ${ref.field_type.replaceAll('_', ' ')}` : ''}</small>{ref.block_id && <button className="text-link" type="button" onClick={() => { setHighlightedBlock(ref.block_id || ''); window.document.getElementById(`evidence-block-${CSS.escape(ref.block_id || '')}`)?.scrollIntoView({ behavior: 'smooth', block: 'center' }); }}>Locate cited OCR block</button>}</article>)}</div> : <p className="panel-foot">No stored finding cites this source page.</p>}</section>}
    {document && <section className="panel evidence-visual-findings"><div className="panel-head"><div><h3>Saved visual observations</h3><p>Observations are automated candidates tied to the stored source page.</p></div><Link className="text-link" to={`/investigations/${investigationId}/visual`}>Open Visual Forensics</Link></div>{visualRuns.isLoading ? <p role="status">Loading persisted visual analysis…</p> : visualRuns.isError ? <p role="alert">Visual run history is unavailable.</p> : observations.length ? <div className="evidence-observation-list">{observations.map((item) => <article key={item.finding_id}><div><b>{item.check_type.replaceAll('_', ' ')} · page {item.page_number}</b><span className="badge blue">Candidate · review required</span></div><p>{item.explanation}</p><small>Run {currentRun?.id.slice(0, 8)} · {item.evidence_ref.block_id ? `OCR block ${item.evidence_ref.block_id}` : 'Region coordinates recorded'}</small><details><summary>Measured metrics</summary><pre>{JSON.stringify(item.metrics, null, 2)}</pre></details></article>)}</div> : <p className="panel-foot">No saved visual observations are available for this page. No anomaly is inferred from the preview.</p>}</section>}
    <p className="investigation-data-note"><ShieldAlert size={14}/> The source file is shown as stored. OCR text and visual observations are derived analysis and should be checked against the original evidence.</p>
  </div>;
}

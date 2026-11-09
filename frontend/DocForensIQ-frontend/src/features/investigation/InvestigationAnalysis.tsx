import { useMemo, useState, type FormEvent } from 'react';
import { ArrowRight, Check, Clock3, Download, Eye, FileImage, FileSearch, Fingerprint, GitBranch, Search, ShieldAlert } from 'lucide-react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { Link } from 'react-router-dom';
import { ApiError, apiRequest } from '../../services/apiClient';

export type AnalysisSection = 'correlation' | 'findings' | 'timeline' | 'visual' | 'search' | 'dossier' | 'audit' | 'ocr-evaluation';

interface EvidenceRef {
  document_id?: string;
  filename?: string;
  page_number?: number | null;
  block_id?: string | null;
  quoted_text?: string;
  source_sha256?: string | null;
  field_type?: string;
  related_total?: string;
}

interface FindingItem {
  id: string;
  finding_type: string;
  title: string;
  description: string;
  severity: string;
  confidence: number;
  status: string;
  analyzer_name: string;
  analyzer_version: string;
  method: string;
  evidence_refs: EvidenceRef[];
  limitations: string;
  review_notes: string | null;
}

interface CorrelationResult {
  status: string;
  method: string;
  summary: { documents_total: number; documents_with_ocr: number; documents_pending: number; documents_with_processing_errors: number; identifier_mentions: number; candidate_entities: number; total_links: number; candidate_links: number; confirmed_links: number; rejected_links: number; unreviewed_links: number; contradictions: number };
  links: Array<{ id: string; relation_type: string; normalized_identifier: string; identifier_type: string; document_ids: string[]; document_count: number; rank: number; rank_kind: string; method: string; explanation: string; review_status: string; review_reason?: string | null; reviewed_at?: string | null; evidence_refs: EvidenceRef[] }>;
  comparisons: Array<{ id: string; field: string; value: string; status: string; explanation: string; documents: Array<{ document_id: string; filename: string; total: { raw: string; evidence_ref: EvidenceRef } | null; dates: Array<{ raw: string; interpreted: string | null; ambiguous: boolean; evidence_ref: EvidenceRef }> }> }>;
  contradictions: Array<{ id: string; title: string; description: string; method: string; evidence_refs: EvidenceRef[]; limitations: string }>;
  limitations: string;
}

interface TimelineItem {
  id: string;
  raw_date: string;
  interpreted_date: string | null;
  ambiguous: boolean;
  timezone_known: boolean;
  description: string;
  confidence: number;
  evidence_refs: EvidenceRef[];
  annotation: string | null;
}

interface MetricValue {
  value?: number | null;
  status?: string;
  reason?: string | null;
}

interface EvaluationPayload {
  id: string;
  dataset_name: string;
  metrics: {
    sample_count: number;
    exact_match_accuracy: number | null;
    cer_mean: number | null;
    wer_mean: number | null;
    detection: { status: string; reason?: string | null; precision: number | null; recall: number | null; f1: number | null };
    latency: { status: string; reason: string };
    resources: { status: string; reason: string };
  };
  samples: Array<{ id: string; exact_match: boolean; ground_truth: string; hypothesis: string; cer: MetricValue; wer: MetricValue; detection: { status: string; reason?: string | null } }>;
}

const SAMPLE_TEMPLATE = `[
  {
    "id": "sample-1",
    "document_type": "invoice",
    "quality": "clean",
    "ground_truth": "Invoice INV-10001 Total 100.00",
    "hypothesis": "Invoice INV-10001 Total 100.00",
    "ground_truth_boxes": [[0, 0, 10, 10]],
    "predicted_boxes": [[0, 0, 10, 10]]
  }
]`;

function tone(status: string) {
  if (status === 'accepted') return 'green';
  if (status === 'rejected' || status === 'dismissed') return 'neutral';
  if (status === 'needs_review' || status === 'high') return 'amber';
  if (status === 'medium') return 'amber';
  return 'blue';
}

function formatMetric(value: number | null | undefined, digits = 3) {
  return value == null ? 'Unavailable' : value.toFixed(digits);
}

export default function InvestigationAnalysis({ investigationId, section }: { investigationId: string; section: AnalysisSection }) {
  const queryClient = useQueryClient();
  const [error, setError] = useState('');
  const [statusFilter, setStatusFilter] = useState('');
  const [notes, setNotes] = useState<Record<string, string>>({});
  const [query, setQuery] = useState('');
  const [datasetName, setDatasetName] = useState('Local comparison');
  const [sampleText, setSampleText] = useState(SAMPLE_TEMPLATE);
  const [activeRun, setActiveRun] = useState<EvaluationPayload | null>(null);
  const [correlationReason, setCorrelationReason] = useState('');

  const correlation = useQuery({
    queryKey: ['correlation', investigationId],
    queryFn: () => apiRequest<CorrelationResult>(`/investigations/${investigationId}/correlation`),
    enabled: section === 'correlation',
  });

  const findings = useQuery({
    queryKey: ['findings', investigationId, statusFilter],
    queryFn: () => apiRequest<{ items: FindingItem[] }>(`/investigations/${investigationId}/findings${statusFilter ? `?status=${statusFilter}` : ''}`),
    enabled: section === 'findings' || section === 'visual',
  });
  const timeline = useQuery({
    queryKey: ['timeline', investigationId],
    queryFn: () => apiRequest<{ items: TimelineItem[]; note: string }>(`/investigations/${investigationId}/timeline`),
    enabled: section === 'timeline',
  });
  const audit = useQuery({
    queryKey: ['audit', investigationId],
    queryFn: () => apiRequest<{ items: Array<{ id: string; action: string; target_type: string; target_id: string; previous_hash: string; event_hash: string; created_at: string; details: Record<string, unknown> }>; verification: { valid: boolean; event_count: number; verified_count: number; broken_event_id: string | null; reason: string | null }; note: string }>(`/investigations/${investigationId}/audit`),
    enabled: section === 'audit',
  });
  const dossiers = useQuery({
    queryKey: ['dossiers', investigationId],
    queryFn: () => apiRequest<{ items: Array<{ id: string; version: number; created_at: string }> }>(`/investigations/${investigationId}/dossiers`),
    enabled: section === 'dossier',
  });
  const evaluations = useQuery({
    queryKey: ['ocr-evaluations', investigationId],
    queryFn: () => apiRequest<{ items: Array<{ id: string; dataset_name: string; created_at: string; metrics: EvaluationPayload['metrics'] }> }>(`/investigations/${investigationId}/ocr-evaluations`),
    enabled: section === 'ocr-evaluation',
  });

  const review = useMutation({
    mutationFn: ({ id, status, review_notes }: { id: string; status: string; review_notes: string }) => apiRequest<FindingItem>(`/investigations/${investigationId}/findings/${id}`, {
      method: 'PATCH',
      body: JSON.stringify({ status, review_notes: review_notes || null }),
    }),
    onSuccess: async () => {
      setError('');
      await queryClient.invalidateQueries({ queryKey: ['findings', investigationId] });
    },
    onError: (reason) => setError(reason instanceof ApiError ? reason.message : 'The review could not be saved.'),
  });
  const annotate = useMutation({
    mutationFn: ({ id, annotation }: { id: string; annotation: string }) => apiRequest(`/investigations/${investigationId}/timeline/${id}`, {
      method: 'PATCH',
      body: JSON.stringify({ annotation }),
    }),
    onSuccess: async () => {
      setError('');
      await queryClient.invalidateQueries({ queryKey: ['timeline', investigationId] });
    },
    onError: (reason) => setError(reason instanceof ApiError ? reason.message : 'The note could not be saved.'),
  });
  const runAnalysis = useMutation({
    mutationFn: () => apiRequest<{ candidate_findings_written: number; documents_with_ocr: number }>(`/investigations/${investigationId}/analysis-runs`, { method: 'POST' }),
    onSuccess: async () => {
      setError('');
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ['findings', investigationId] }),
        queryClient.invalidateQueries({ queryKey: ['timeline', investigationId] }),
        queryClient.invalidateQueries({ queryKey: ['relationships', investigationId] }),
        queryClient.invalidateQueries({ queryKey: ['audit', investigationId] }),
      ]);
    },
    onError: (reason) => setError(reason instanceof ApiError ? reason.message : 'Analysis could not be run.'),
  });
  const runCorrelation = useMutation({
    mutationFn: () => apiRequest<CorrelationResult>(`/investigations/${investigationId}/correlation-runs`, { method: 'POST' }),
    onSuccess: async (result) => {
      queryClient.setQueryData(['correlation', investigationId], result);
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ['findings', investigationId] }),
        queryClient.invalidateQueries({ queryKey: ['timeline', investigationId] }),
        queryClient.invalidateQueries({ queryKey: ['relationships', investigationId] }),
        queryClient.invalidateQueries({ queryKey: ['audit', investigationId] }),
      ]);
    },
    onError: (reason) => setError(reason instanceof ApiError ? reason.message : 'Cross-document correlation could not be run.'),
  });
  const reviewCorrelation = useMutation({
    mutationFn: ({ id, status }: { id: string; status: 'confirmed' | 'rejected' }) => apiRequest<{ id: string; review_status: string; reviewed_at: string; review_reason: string }>(`/investigations/${investigationId}/correlation/${encodeURIComponent(id)}/review`, {
      method: 'PATCH',
      body: JSON.stringify({ status, reason: correlationReason }),
    }),
    onSuccess: async () => {
      setCorrelationReason('');
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ['correlation', investigationId] }),
        queryClient.invalidateQueries({ queryKey: ['relationships', investigationId] }),
        queryClient.invalidateQueries({ queryKey: ['audit', investigationId] }),
      ]);
    },
    onError: (reason) => setError(reason instanceof ApiError ? reason.message : 'The correlation review could not be saved.'),
  });
  const search = useMutation({
    mutationFn: () => apiRequest<{ answer: string; citations: Array<{ kind: string; document_id?: string; filename?: string; page_number?: number | null; block_id?: string | null; quoted_text?: string; finding_id?: string; title?: string; status?: string }>; llm: { used: boolean; status: string; reason: string } }>(`/investigations/${investigationId}/search`, {
      method: 'POST',
      body: JSON.stringify({ query }),
    }),
  });
  const createDossier = useMutation({
    mutationFn: () => apiRequest<{ id: string; version: number; html: string; snapshot: { label: string; ai_narrative: string | null; investigator_conclusion: string | null } }>(`/investigations/${investigationId}/dossiers`, { method: 'POST' }),
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: ['dossiers', investigationId] });
      await queryClient.invalidateQueries({ queryKey: ['audit', investigationId] });
    },
    onError: (reason) => setError(reason instanceof ApiError ? reason.message : 'The dossier could not be created.'),
  });

  const visibleFindings = useMemo(() => {
    const items = findings.data?.items ?? [];
    if (section === 'visual') return items.filter((item) => item.finding_type.startsWith('visual_'));
    return items;
  }, [findings.data, section]);

  async function submitEvaluation(event: FormEvent) {
    event.preventDefault();
    setError('');
    let samples: unknown;
    try {
      samples = JSON.parse(sampleText);
    } catch {
      setError('Sample JSON could not be parsed.');
      return;
    }
    try {
      const created = await apiRequest<EvaluationPayload>(`/investigations/${investigationId}/ocr-evaluations`, {
        method: 'POST',
        body: JSON.stringify({ dataset_name: datasetName, configuration: { source: 'investigator_supplied_text' }, samples }),
      });
      setActiveRun(created);
      await queryClient.invalidateQueries({ queryKey: ['ocr-evaluations', investigationId] });
    } catch (reason) {
      setError(reason instanceof ApiError ? reason.message : 'The evaluation could not be saved.');
    }
  }

  function downloadJson(filename: string, value: unknown) {
    const blob = new Blob([JSON.stringify(value, null, 2)], { type: 'application/json' });
    const link = document.createElement('a');
    link.href = URL.createObjectURL(blob);
    link.download = filename;
    link.click();
    URL.revokeObjectURL(link.href);
  }

  const runButton = <button className="button primary" type="button" disabled={runAnalysis.isPending} onClick={() => runAnalysis.mutate()}>{runAnalysis.isPending ? 'Running rules…' : 'Run evidence rules'}</button>;

  return <div className="dashboard-page">
    {error && <div className="auth-error" role="alert">{error}</div>}
    {section === 'correlation' && <>
      <div className="panel-head"><div><h2>Cross-document intelligence</h2><p>Exact labeled identifiers create candidate links. Values retain their source citations; a link is not proof that records describe one transaction or party.</p></div><button className="button primary" type="button" disabled={runCorrelation.isPending} onClick={() => runCorrelation.mutate()}><GitBranch size={14}/>{runCorrelation.isPending ? 'Re-running…' : 'Re-run correlation'}</button></div>
      {correlation.isLoading ? <div className="panel" role="status">Loading persisted OCR evidence…</div> : correlation.isError ? <div className="panel" role="alert">Cross-document results could not be loaded. <button type="button" className="text-link" onClick={() => void correlation.refetch()}>Retry</button></div> : correlation.data && <>
        <div className="stat-grid">
          <article className="stat-card"><span className="stat-label">DOCUMENTS WITH OCR</span><strong>{correlation.data.summary.documents_with_ocr} / {correlation.data.summary.documents_total}</strong></article>
          <article className="stat-card"><span className="stat-label">IDENTIFIER MENTIONS</span><strong>{correlation.data.summary.identifier_mentions}</strong></article>
          <article className="stat-card"><span className="stat-label">CANDIDATE ENTITIES</span><strong>{correlation.data.summary.candidate_entities}</strong></article>
          <article className="stat-card"><span className="stat-label">CONFIRMED LINKS</span><strong>{correlation.data.summary.confirmed_links}</strong></article>
          <article className="stat-card"><span className="stat-label">UNREVIEWED LINKS</span><strong>{correlation.data.summary.unreviewed_links}</strong></article>
          <article className="stat-card"><span className="stat-label">CONTRADICTIONS</span><strong>{correlation.data.summary.contradictions}</strong></article>
          <article className="stat-card"><span className="stat-label">DOCUMENTS WITH ERRORS</span><strong>{correlation.data.summary.documents_with_processing_errors}</strong></article>
        </div>
        {correlation.data.status !== 'ready' && <div className="panel notice" role="status">{correlation.data.status === 'no_documents' ? 'Upload source documents to begin.' : 'No stored OCR text is available yet. Correlation will become available after OCR completes.'}</div>}
        {correlation.data.summary.documents_pending > 0 && <div className="notice" role="status">{correlation.data.summary.documents_pending} document(s) are still processing. Current links include completed OCR documents; refresh this view after processing completes.</div>}
        {correlation.data.summary.documents_with_processing_errors > 0 && <div className="notice" role="status">{correlation.data.summary.documents_with_processing_errors} document(s) have processing errors and may be absent from comparisons.</div>}
        {correlation.data.links.length === 0 && correlation.data.status === 'ready' ? <div className="panel dashboard-empty"><GitBranch size={22}/><h2>No exact cross-document identifier matches</h2><p>Names, dates, or amounts alone do not create links. New OCR results are included automatically when this view refreshes.</p></div> : correlation.data.links.map((link) => <article className="panel" key={link.id}>
          <div className="panel-head"><div><span className={`badge ${link.review_status === 'confirmed' ? 'green' : link.review_status === 'rejected' ? 'neutral' : 'blue'}`}>{link.review_status === 'candidate' ? 'Unreviewed candidate' : link.review_status}</span><h2>{link.normalized_identifier}</h2><p>{link.identifier_type.replaceAll('_', ' ')} · {link.document_count} documents · heuristic rank {link.rank.toFixed(2)} · {link.method}</p></div></div>
          <p>{link.explanation}</p>
          {link.review_reason && <p className="muted">Review note: {link.review_reason}</p>}
          <div className="ocr-block-list">{link.evidence_refs.map((ref, index) => <article key={`${link.id}-${ref.document_id}-${index}`}><b>{ref.filename || ref.document_id}</b><small>{ref.field_type}{ref.page_number == null ? '' : ` · page ${ref.page_number}`}{ref.block_id ? ` · ${ref.block_id}` : ''}</small><p>{ref.quoted_text || 'No source snippet available.'}</p>{ref.document_id && <Link className="text-link" to={`/investigations/${investigationId}?doc=${ref.document_id}`}>Open cited document</Link>}</article>)}</div>
          {link.review_status === 'candidate' && <><label className="auth-label">Review reason<textarea value={correlationReason} onChange={(event) => setCorrelationReason(event.target.value)} maxLength={4000} rows={2} placeholder="Record why this link should be confirmed or rejected"/></label><div className="create-actions"><button className="button secondary small" type="button" disabled={reviewCorrelation.isPending || !correlationReason.trim()} onClick={() => reviewCorrelation.mutate({ id: link.id, status: 'confirmed' })}>Confirm candidate</button><button className="button secondary small" type="button" disabled={reviewCorrelation.isPending || !correlationReason.trim()} onClick={() => reviewCorrelation.mutate({ id: link.id, status: 'rejected' })}>Reject candidate</button></div></>}
        </article>)}
        {correlation.data.comparisons.map((comparison) => <article className="panel" key={`comparison-${comparison.id}`}><div className="panel-head"><div><h2>Comparison · {comparison.value}</h2><p>{comparison.explanation}</p></div><span className={`badge ${comparison.status === 'value_conflict' ? 'amber' : 'blue'}`}>{comparison.status.replaceAll('_', ' ')}</span></div><div className="table-wrap"><table className="dashboard-table"><thead><tr><th>SOURCE DOCUMENT</th><th>LABELED TOTAL</th><th>DOCUMENT DATES</th></tr></thead><tbody>{comparison.documents.map((doc) => <tr key={doc.document_id}><td><Link className="text-link" to={`/investigations/${investigationId}?doc=${doc.document_id}`}>{doc.filename}</Link></td><td>{doc.total ? <>{doc.total.raw}<small>{doc.total.evidence_ref.page_number == null ? '' : ` · page ${doc.total.evidence_ref.page_number}`}</small></> : 'Not extracted'}{doc.total?.evidence_ref.block_id && <small> · {doc.total.evidence_ref.block_id}</small>}</td><td>{doc.dates.length ? doc.dates.map((date) => `${date.raw}${date.ambiguous ? ' (ambiguous)' : ''}`).join(', ') : 'Not extracted'}</td></tr>)}</tbody></table></div></article>)}
        {correlation.data.contradictions.map((finding) => <article className="panel" key={finding.id}><div className="panel-head"><div><span className="badge amber">Candidate discrepancy</span><h2>{finding.title}</h2><p>{finding.method}</p></div></div><p>{finding.description}</p><p className="muted">{finding.limitations}</p><div className="ocr-block-list">{finding.evidence_refs.map((ref, index) => <article key={`${finding.id}-${index}`}><b>{ref.filename}</b><small>{ref.field_type}{ref.page_number == null ? '' : ` · page ${ref.page_number}`}{ref.block_id ? ` · ${ref.block_id}` : ''}</small><p>{ref.quoted_text}</p>{ref.document_id && <Link className="text-link" to={`/investigations/${investigationId}?doc=${ref.document_id}`}>Open source</Link>}</article>)}</div></article>)}
        <p className="muted">{correlation.data.limitations} · Results use {correlation.data.method}. Candidate link counts are not confirmed identity matches.</p>
      </>}
    </>}
    {section === 'findings' && <>
      <div className="panel-head"><div><h2>Findings for review</h2><p>Rules create candidates. Accepting one records your review; it does not prove fraud or authenticity.</p></div><div className="head-controls"><label className="auth-label">Status<select aria-label="Filter findings by status" value={statusFilter} onChange={(event) => setStatusFilter(event.target.value)}><option value="">All</option><option value="candidate">Candidate</option><option value="accepted">Accepted</option><option value="needs_review">Needs review</option><option value="rejected">Rejected</option><option value="dismissed">Dismissed</option></select></label>{runButton}</div></div>
      {findings.isLoading ? <div className="panel" role="status">Loading findings…</div> : findings.isError ? <div className="panel" role="alert">Findings could not be loaded.</div> : visibleFindings.length === 0 ? <div className="panel dashboard-empty"><FileSearch size={22}/><h2>No findings yet</h2><p>Run evidence rules after OCR finishes. Connections and conflicts appear here only when the stored text supports them.</p>{runButton}</div> : visibleFindings.map((finding) => <article className="panel" key={finding.id}>
        <div className="panel-head"><div><span className={`badge ${tone(finding.severity)}`}>{finding.severity}</span> <span className={`badge ${tone(finding.status)}`}>{finding.status.replaceAll('_', ' ')}</span><h2>{finding.title}</h2><p>{finding.analyzer_name} {finding.analyzer_version} · {finding.method} · engine-style score {finding.confidence.toFixed(2)}, not a calibrated probability</p></div></div>
        <p>{finding.description}</p>
        <p className="muted">{finding.limitations}</p>
        <div className="ocr-block-list">{finding.evidence_refs.map((ref, index) => <article key={`${finding.id}-${index}`}><b>{ref.filename || ref.document_id}</b><small>{ref.field_type || 'source'}{ref.page_number == null ? '' : ` · page ${ref.page_number}`}{ref.block_id ? ` · ${ref.block_id}` : ''}</small><p>{ref.quoted_text || 'No excerpt stored.'}</p>{ref.document_id && <Link className="text-link" to={`/investigations/${investigationId}?doc=${ref.document_id}`}>Open document</Link>}</article>)}</div>
        <label className="auth-label">Review note<textarea value={notes[finding.id] ?? finding.review_notes ?? ''} onChange={(event) => setNotes((current) => ({ ...current, [finding.id]: event.target.value }))} rows={2}/></label>
        <div className="create-actions">
          {(['accepted', 'needs_review', 'rejected', 'dismissed'] as const).map((status) => <button key={status} className="button secondary small" type="button" disabled={review.isPending} onClick={() => review.mutate({ id: finding.id, status, review_notes: notes[finding.id] ?? finding.review_notes ?? '' })}><Check size={13}/> {status.replaceAll('_', ' ')}</button>)}
        </div>
      </article>)}
    </>}
    {section === 'timeline' && <>
      <div className="panel-head"><div><h2>Timeline</h2><p>{timeline.data?.note || 'Dates are taken from OCR text. Ambiguous numeric dates are not silently resolved.'}</p></div>{runButton}</div>
      {timeline.isLoading ? <div className="panel" role="status">Loading timeline…</div> : (timeline.data?.items.length ?? 0) === 0 ? <div className="panel dashboard-empty"><Clock3 size={22}/><h2>No dated events</h2><p>Run evidence rules after OCR. Only dates present in the recognized text are listed.</p></div> : <div className="timeline-list">{timeline.data?.items.map((event) => <article className="panel" key={event.id}><div className="panel-head"><div><h3>{event.raw_date}</h3><p>{event.ambiguous ? 'Ambiguous day/month order · not interpreted' : event.interpreted_date || 'No calendar interpretation'} · timezone unknown</p></div><span className={`badge ${event.ambiguous ? 'amber' : 'blue'}`}>{event.ambiguous ? 'Ambiguous' : 'Interpreted'}</span></div><p>{event.description}</p>{event.evidence_refs[0]?.document_id && <Link className="text-link" to={`/investigations/${investigationId}?doc=${event.evidence_refs[0].document_id}`}>Open source document</Link>}{event.annotation && <p><b>Investigator note:</b> {event.annotation}</p>}<form className="create-actions" onSubmit={(formEvent) => { formEvent.preventDefault(); const data = new FormData(formEvent.currentTarget); const annotation = String(data.get('annotation') || ''); if (annotation.trim()) annotate.mutate({ id: event.id, annotation }); }}><input className="text-input" name="annotation" aria-label={`Note for ${event.raw_date}`} placeholder="Add an investigator note" defaultValue={event.annotation ?? ''}/><button className="button secondary small" type="submit">Save note</button></form></article>)}</div>}
    </>}
    {section === 'visual' && <>
      <section className="visual-review">
        <div className="visual-review-heading">
          <div className="visual-review-mark"><Eye size={21}/></div>
          <div><p className="visual-review-eyebrow">Source inspection</p><h2>Visual evidence review</h2><p>Inspect original pages alongside OCR regions. Automated image-forensics checks are not available in this build.</p></div>
          <span className="badge neutral">Automated checks unavailable</span>
        </div>
        <div className="visual-review-grid">
          <article className="visual-review-source">
            <div className="visual-review-icon"><FileImage size={19}/></div>
            <h3>Review the original</h3>
            <p>Open a document to inspect its original image or PDF. Image pages can show OCR region overlays in source coordinates.</p>
            <Link className="visual-review-link" to={`/investigations/${investigationId}`}><span>Open document review</span><ArrowRight size={15}/></Link>
          </article>
          <div className="visual-review-checks">
            <h3>Automated visual checks</h3>
            {['Font and baseline inconsistency', 'Copy-move or duplicated image regions', 'Compression or resampling differences', 'PDF object and layer anomalies'].map((item) => <div className="visual-review-check" key={item}><span>{item}</span><span className="badge neutral">Not implemented</span></div>)}
          </div>
        </div>
        <p className="visual-review-boundary"><ShieldAlert size={15}/> OCR confidence and identical file hashes are separate signals. Neither is visual analysis, and neither establishes authenticity.</p>
      </section>
      {findings.isLoading && <div className="panel" role="status">Loading visual findings…</div>}
      {findings.isError && <div className="notice" role="alert">Visual findings could not be loaded. Retry this page to check again.</div>}
      {findings.isSuccess && visibleFindings.map((finding) => <article className="panel" key={finding.id}><h2>{finding.title}</h2><p>{finding.description}</p><p className="muted">{finding.limitations}</p></article>)}
    </>}
    {section === 'search' && <form className="panel" onSubmit={(event) => { event.preventDefault(); search.mutate(); }}><div className="panel-head"><div><h2>Evidence search</h2><p>Searches stored OCR text and finding titles in this investigation only.</p></div></div><label className="auth-label">Search text<input value={query} onChange={(event) => setQuery(event.target.value)} required placeholder="Invoice number, name, or amount"/></label><button className="button primary" type="submit" disabled={search.isPending}><Search size={14}/> {search.isPending ? 'Searching…' : 'Search evidence'}</button>
      {search.data && <div><p>{search.data.answer}</p><p className="muted">Language model: {search.data.llm.status}. {search.data.llm.reason}</p><div className="ocr-block-list">{search.data.citations.map((citation, index) => <article key={`${citation.kind}-${index}`}><b>{citation.filename || citation.title || citation.kind}</b><small>{citation.page_number == null ? citation.kind : `Page ${citation.page_number}`}</small><p>{citation.quoted_text || citation.status}</p>{citation.document_id && <Link className="text-link" to={`/investigations/${investigationId}?doc=${citation.document_id}`}>Open document</Link>}</article>)}</div></div>}
    </form>}
    {section === 'dossier' && <>
      <div className="panel-head"><div><h2>Dossier snapshots</h2><p>Each export is a stored snapshot. Generated narrative is separate from an investigator conclusion, and this builder does not invent a conclusion.</p></div><button className="button primary" type="button" disabled={createDossier.isPending} onClick={() => createDossier.mutate()}><Download size={14}/> {createDossier.isPending ? 'Building snapshot…' : 'Create snapshot'}</button></div>
      {createDossier.data && <div className="panel"><div className="panel-head"><div><h3>Version {createDossier.data.version}</h3><p>{createDossier.data.snapshot.label}</p></div><button className="button secondary small" type="button" onClick={() => downloadJson(`dossier-v${createDossier.data?.version}.json`, createDossier.data)}>Download JSON</button></div><p>Investigator conclusion: {createDossier.data.snapshot.investigator_conclusion || 'Not recorded.'}</p><p>AI narrative: {createDossier.data.snapshot.ai_narrative || 'Not included.'}</p><iframe title="Dossier preview" sandbox="" className="dossier-frame" srcDoc={createDossier.data.html}/></div>}
      {(dossiers.data?.items.length ?? 0) === 0 ? <div className="panel dashboard-empty"><Fingerprint size={22}/><h2>No snapshot yet</h2><p>Create a snapshot when you want a versioned HTML and JSON record of the current evidence, findings, and limitations.</p></div> : <ul className="activity-list">{dossiers.data?.items.map((item) => <li key={item.id}>Version {item.version} · {new Date(item.created_at).toLocaleString()}</li>)}</ul>}
    </>}
    {section === 'audit' && <>
      <div className="panel-head"><div><h2>Audit chain</h2><p>{audit.data?.note || 'Upload, analysis, review, and dossier events are hash-chained.'}</p></div></div>
      {audit.data?.verification && <div className={`audit-verification ${audit.data.verification.valid ? 'is-valid' : 'is-invalid'}`} role={audit.data.verification.valid ? 'status' : 'alert'}><Fingerprint size={16}/><span><b>{audit.data.verification.valid ? 'Chain verified' : 'Chain verification failed'}</b><small>{audit.data.verification.valid ? `${audit.data.verification.verified_count} of ${audit.data.verification.event_count} events match the stored links and hashes.` : audit.data.verification.reason}</small></span></div>}
      {audit.isLoading ? <div className="panel" role="status">Loading audit…</div> : audit.isError ? <div className="panel" role="alert">Audit history could not be loaded.</div> : (audit.data?.items.length ?? 0) === 0 ? <div className="panel dashboard-empty"><Fingerprint size={22}/><h2>No audit events</h2><p>Uploading evidence writes the first event.</p></div> : <div className="table-wrap"><table className="dashboard-table"><thead><tr><th>WHEN</th><th>ACTION</th><th>TARGET</th><th>EVENT HASH</th></tr></thead><tbody>{audit.data?.items.map((item) => <tr key={item.id}><td>{new Date(item.created_at).toLocaleString()}</td><td>{item.action}</td><td>{item.target_type}</td><td className="mono">{item.event_hash.slice(0, 16)}…</td></tr>)}</tbody></table></div>}
    </>}
    {section === 'ocr-evaluation' && <div className="dashboard-columns">
      <form className="panel" onSubmit={(event) => void submitEvaluation(event)}><div className="panel-head"><div><h2>OCR evaluation</h2><p>Metrics are calculated from ground truth you supply. Latency and GPU figures stay unavailable because this screen does not run PaddleOCR.</p></div></div>
        <label className="auth-label">Dataset name<input value={datasetName} onChange={(event) => setDatasetName(event.target.value)} required/></label>
        <label className="auth-label">Samples JSON<textarea value={sampleText} onChange={(event) => setSampleText(event.target.value)} rows={14} spellCheck={false}/></label>
        <button className="button primary" type="submit">Calculate metrics</button>
      </form>
      <section className="panel"><div className="panel-head"><div><h2>Results</h2><p>{activeRun ? activeRun.dataset_name : 'No run selected yet.'}</p></div>{activeRun && <button className="button secondary small" type="button" onClick={() => downloadJson(`${activeRun.dataset_name}-ocr-evaluation.json`, activeRun)}>Download report</button>}</div>
        {!activeRun ? <p className="muted">Submit a labeled sample set. Empty ground truth is reported as an undefined error rate, not as a perfect score.</p> : <>
          <div className="stat-grid"><article className="stat-card"><span className="stat-label">CER</span><strong>{formatMetric(activeRun.metrics.cer_mean)}</strong></article><article className="stat-card"><span className="stat-label">WER</span><strong>{formatMetric(activeRun.metrics.wer_mean)}</strong></article><article className="stat-card"><span className="stat-label">EXACT MATCH</span><strong>{formatMetric(activeRun.metrics.exact_match_accuracy, 2)}</strong></article><article className="stat-card"><span className="stat-label">DETECTION F1</span><strong>{activeRun.metrics.detection.status === 'computed' ? formatMetric(activeRun.metrics.detection.f1) : 'Unavailable'}</strong></article></div>
          <p className="muted">{activeRun.metrics.detection.reason || `Detection precision ${formatMetric(activeRun.metrics.detection.precision)} · recall ${formatMetric(activeRun.metrics.detection.recall)}`}</p>
          <p className="muted">{activeRun.metrics.latency.reason} {activeRun.metrics.resources.reason}</p>
          <div className="table-wrap"><table><thead><tr><th>SAMPLE</th><th>EXACT</th><th>CER</th><th>EXPECTED</th><th>RECOGNIZED</th></tr></thead><tbody>{activeRun.samples.map((sample) => <tr key={sample.id}><td>{sample.id}</td><td>{sample.exact_match ? 'Yes' : 'No'}</td><td>{sample.cer.status === 'computed' ? formatMetric(sample.cer.value) : sample.cer.reason}</td><td>{sample.ground_truth || '—'}</td><td>{sample.hypothesis || '—'}</td></tr>)}</tbody></table></div>
        </>}
        {(evaluations.data?.items.length ?? 0) > 0 && <div className="panel-foot">Saved runs: {evaluations.data?.items.map((item) => item.dataset_name).join(', ')}</div>}
      </section>
    </div>}
  </div>;
}

import { useEffect, useMemo, useState, type FormEvent } from 'react';
import { AlignLeft, Check, ChevronLeft, ChevronRight, Clock3, Cpu, Download, Eye, FileImage, FileSearch, FileText, Fingerprint, GitBranch, Image as ImageIcon, Layers3, Search, ShieldAlert, SlidersHorizontal, ZoomIn, ZoomOut } from 'lucide-react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { Link } from 'react-router-dom';
import { ApiError, apiBlob, apiRequest } from '../../services/apiClient';

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
  bounding_box?: { x0?: number; y0?: number; x1?: number; y1?: number } | null;
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
  comparisons: Array<{ id: string; field: string; value: string; status: string; explanation: string; documents: Array<{ document_id: string; filename: string; total: { raw: string; value?: number; evidence_ref: EvidenceRef } | null; dates: Array<{ raw: string; interpreted: string | null; ambiguous: boolean; evidence_ref: EvidenceRef }> }> }>;
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

interface AnalysisDiagnostics {
  summary: { documents_inspected: number; findings_produced: number; timeline_events_produced: number; graph_nodes_produced: number; findings_by_type: Record<string, number> };
  data_flow: { source: string; structured_extraction_used_for_findings: boolean; structured_extraction_note: string };
  documents: Array<{ document_id: string; filename: string; ocr_text_characters: number; identifiers_extracted: Array<{ value: string; entity_type: string; block_id: string | null; page_index: number | null; text: string }>; financial_fields_extracted: Array<{ field_type: string; raw: string; normalized: number; normalization_warnings: string[]; block_id: string | null; page_number: number | null }>; observed_labeled_fields: Array<{ field_type: string; raw: string; block_id: string | null; page_index: number | null; text: string }>; dates_extracted: unknown[]; ocr_quality: { status: string; mean_confidence: number | null; low_confidence_block_ids: string[]; threshold: number }; structured_entities: Array<{ entity_type: string; mention_text: string; source_block_ids: string[] }> }>;
  rules: Array<{ rule_id: string; description: string; executed: boolean; documents_inspected: string[]; fields_extracted: unknown[]; comparisons_performed: unknown[]; rules_matched: number; rules_skipped: Array<Record<string, unknown>> }>;
}

interface VisualDocument { id: string; filename: string; content_type: string; processing_status: string; sha256?: string | null; }
interface VisualOcrBlock { block_id: string; page_index?: number; text: string; engine_bbox?: unknown; engine_confidence?: number | null; }
interface EngineHealth { engine: string; feature_enabled: boolean; available: boolean; reason: string | null; model_version: string | null; checkpoint_sha256: string | null; device: string | null; }
interface VisualRegionBox { x0: number; y0: number; x1: number; y1: number; }
interface VisualCandidateRegionItem { id: string; region_index: number; bbox_pixel: VisualRegionBox; bbox_pdf_points: VisualRegionBox; area_px: number; mean_localization_score: number | null; max_localization_score: number | null; mean_reliability_value: number | null; threshold_config: Record<string, unknown>; postprocess_version: string; linked_ocr_block_ids: string[]; review_status?: string; review_notes?: string | null; }
interface VisualRunPageItem { id: string; run_id: string; page_number: number; status: string; engine: string | null; engine_version: string | null; checkpoint_sha256: string | null; device: string | null; source_page_width_points: number | null; source_page_height_points: number | null; render_width_px: number | null; render_height_px: number | null; render_dpi: number | null; page_rotation: number | null; transform: Record<string, unknown>; integrity_score: number | null; localization_artifact_id: string | null; reliability_artifact_id: string | null; noiseprint_artifact_id: string | null; diagnostics: Record<string, unknown>; regions: VisualCandidateRegionItem[]; }
interface VisualRun { id: string; document_id: string; status: string; result: { run_kind?: 'new' | 'retry'; page_count?: number; engine?: EngineHealth | null; checks?: Array<{ check_type: string; status: string; algorithm: string; pages_analyzed?: number[]; metrics?: Record<string, unknown>; skipped?: Array<{ reason?: string }> }>; observations?: Array<{ finding_id: string; investigation_id: string; document_id: string; page_number: number; regions: Array<{ x0: number; y0: number; x1: number; y1: number }>; check_type: string; algorithm: string; metrics: Record<string, unknown>; explanation: string; limitations: string; evidence_ref: EvidenceRef & { bounding_box?: Record<string, number>; regions?: Array<Record<string, number>> }; review_status: string }> }; error_detail?: string | null; created_at: string; started_at?: string | null; completed_at?: string | null; }

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

function visualBounds(value: unknown): { left: number; top: number; width: number; height: number } | null {
  if (!Array.isArray(value)) return null;
  const points: number[][] | null = value.length === 4 && value.every((item) => typeof item === 'number')
    ? [[value[0] as number, value[1] as number], [value[2] as number, value[3] as number]]
    : value.length >= 4 && value.every((item) => Array.isArray(item) && item.length >= 2 && item.every((part) => typeof part === 'number'))
      ? value as number[][] : null;
  if (!points) return null;
  const xs = points.map((point) => point[0]);
  const ys = points.map((point) => point[1]);
  const left = Math.min(...xs), top = Math.min(...ys), right = Math.max(...xs), bottom = Math.max(...ys);
  return right > left && bottom > top ? { left, top, width: right - left, height: bottom - top } : null;
}

function comparisonStatus(comparison: CorrelationResult['comparisons'][number]) {
  const values = comparison.documents
    .map((document) => document.total?.raw.trim().toLocaleLowerCase())
    .filter((value): value is string => Boolean(value));
  if (values.length < 2) return 'not_available';
  if (comparison.status === 'value_conflict') return 'difference';
  if (new Set(values).size > 1) return 'variation';
  return 'consistent';
}

function comparisonStatusLabel(status: string) {
  return status === 'consistent' ? 'Consistent'
    : status === 'difference' ? 'Difference'
      : status === 'variation' ? 'Variation' : 'Not available';
}

function evidenceUrl(investigationId: string, ref: EvidenceRef) {
  const params = new URLSearchParams({ doc: ref.document_id ?? '' });
  if (ref.page_number != null) params.set('page', String(ref.page_number));
  if (ref.block_id) params.set('block', ref.block_id);
  if (ref.bounding_box) for (const key of ['x0', 'y0', 'x1', 'y1'] as const) {
    const coordinate = ref.bounding_box[key];
    if (typeof coordinate === 'number' && Number.isFinite(coordinate)) params.set(key, String(coordinate));
  }
  return `/investigations/${investigationId}?${params.toString()}`;
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
  const [exportingDossier, setExportingDossier] = useState('');
  const [visualError, setVisualError] = useState('');
  const [comparisonDocumentId, setComparisonDocumentId] = useState('');

  const correlation = useQuery({
    queryKey: ['correlation', investigationId],
    queryFn: () => apiRequest<CorrelationResult>(`/investigations/${investigationId}/correlation`),
    enabled: section === 'correlation',
  });
  const comparisonDocuments = useQuery({
    queryKey: ['comparison-documents', investigationId],
    queryFn: () => apiRequest<VisualDocument[]>(`/investigations/${investigationId}/documents`),
    enabled: section === 'correlation',
  });
  const comparisonDocumentItems = Array.isArray(comparisonDocuments.data) ? comparisonDocuments.data : [];
  const visibleComparisonDocuments = comparisonDocumentId
    ? comparisonDocumentItems.filter((document) => document.id === comparisonDocumentId)
    : comparisonDocumentItems;

  const findings = useQuery({
    queryKey: ['findings', investigationId, statusFilter],
    queryFn: () => apiRequest<{ items: FindingItem[] }>(`/investigations/${investigationId}/findings${statusFilter ? `?status=${statusFilter}` : ''}`),
    enabled: section === 'findings',
  });
  const timeline = useQuery({
    queryKey: ['timeline', investigationId],
    queryFn: () => apiRequest<{ items: TimelineItem[]; note: string }>(`/investigations/${investigationId}/timeline`),
    enabled: section === 'timeline',
  });
  const orderedTimeline = useMemo(() => [...(timeline.data?.items ?? [])].sort((a, b) => {
    const left = a.interpreted_date ? Date.parse(a.interpreted_date) : Number.NaN;
    const right = b.interpreted_date ? Date.parse(b.interpreted_date) : Number.NaN;
    if (Number.isFinite(left) && Number.isFinite(right)) return left - right;
    if (Number.isFinite(left)) return -1;
    if (Number.isFinite(right)) return 1;
    return a.raw_date.localeCompare(b.raw_date);
  }), [timeline.data]);
  const audit = useQuery({
    queryKey: ['audit', investigationId],
    queryFn: () => apiRequest<{ items: Array<{ id: string; action: string; target_type: string; target_id: string; previous_hash: string; event_hash: string; created_at: string; details: Record<string, unknown> }>; verification: { valid: boolean; event_count: number; verified_count: number; broken_event_id: string | null; reason: string | null }; note: string }>(`/investigations/${investigationId}/audit`),
    enabled: section === 'audit',
  });
  const dossiers = useQuery({
    queryKey: ['dossiers', investigationId],
    queryFn: () => apiRequest<{ items: Array<{ id: string; version: number; created_at: string; content_sha256?: string }> }>(`/investigations/${investigationId}/dossiers`),
    enabled: section === 'dossier',
  });
  const evaluations = useQuery({
    queryKey: ['ocr-evaluations', investigationId],
    queryFn: () => apiRequest<{ items: Array<{ id: string; dataset_name: string; created_at: string; metrics: EvaluationPayload['metrics'] }> }>(`/investigations/${investigationId}/ocr-evaluations`),
    enabled: section === 'ocr-evaluation',
  });
  const visualDocuments = useQuery({
    queryKey: ['visual-documents', investigationId],
    queryFn: () => apiRequest<VisualDocument[]>(`/investigations/${investigationId}/documents`),
    enabled: section === 'visual',
  });
  const visualRuns = useQuery({
    queryKey: ['visual-runs', investigationId],
    queryFn: () => apiRequest<{ items: VisualRun[] }>(`/investigations/${investigationId}/visual-analysis-runs`),
    enabled: section === 'visual',
  });
  const [visualDocumentId, setVisualDocumentId] = useState('');
  const [visualPage, setVisualPage] = useState(1);
  const [visualLayer, setVisualLayer] = useState('all');
  const [visualZoom, setVisualZoom] = useState(100);
  const [visualMapOpacity, setVisualMapOpacity] = useState(0.65);
  const [visualImageSize, setVisualImageSize] = useState({ width: 0, height: 0 });
  const [visualCompareMode, setVisualCompareMode] = useState(false);
  const [visualPan, setVisualPan] = useState({ x: 0, y: 0 });
  const [panStart, setPanStart] = useState<{ x: number; y: number; panX: number; panY: number } | null>(null);
  const [selectedVisualRegionId, setSelectedVisualRegionId] = useState('');
  const [selectedOcrBlockId, setSelectedOcrBlockId] = useState('');
  const visualRunsByDocument = useMemo(() => {
    const grouped = new Map<string, VisualRun[]>();
    for (const run of visualRuns.data?.items ?? []) grouped.set(run.document_id, [...(grouped.get(run.document_id) ?? []), run]);
    return grouped;
  }, [visualRuns.data]);
  useEffect(() => {
    const docs = visualDocuments.data ?? [];
    if (docs.length && !docs.some((doc) => doc.id === visualDocumentId)) {
      setVisualDocumentId(docs[0].id);
      setVisualPage(1);
    }
  }, [visualDocuments.data, visualDocumentId]);
  const visualDocument = visualDocuments.data?.find((doc) => doc.id === visualDocumentId);
  const documentVisualHistory = visualRunsByDocument.get(visualDocumentId) ?? [];
  const latestVisualRun = documentVisualHistory[0];
  const visualEngine = useQuery({
    queryKey: ['visual-engine', investigationId],
    queryFn: () => apiRequest<EngineHealth>(`/investigations/${investigationId}/visual-forensics-engine`),
    enabled: section === 'visual',
    retry: false,
  });
  const extractionRuns = useQuery({
    queryKey: ['visual-extraction-runs', investigationId, visualDocumentId],
    queryFn: () => apiRequest<{ items: Array<{ id: string; status: string }> }>(`/investigations/${investigationId}/documents/${visualDocumentId}/extraction-runs`),
    enabled: section === 'visual' && Boolean(visualDocumentId), retry: false,
  });
  const extractionRunId = extractionRuns.data?.items.find((run) => run.status === 'completed')?.id;
  const extractionRun = useQuery({
    queryKey: ['visual-extraction-run', investigationId, visualDocumentId, extractionRunId],
    queryFn: () => apiRequest<{ pages: Array<{ page_number: number; width: number | null; height: number | null }> }>(`/investigations/${investigationId}/documents/${visualDocumentId}/extraction-runs/${extractionRunId}`),
    enabled: section === 'visual' && Boolean(extractionRunId), retry: false,
  });
  const visualPageCount = Math.max(1, latestVisualRun?.result.page_count ?? extractionRun.data?.pages.length ?? 1);
  const visualRunPages = useQuery({
    queryKey: ['visual-run-pages', investigationId, latestVisualRun?.id],
    queryFn: () => apiRequest<{ items: VisualRunPageItem[] }>(`/investigations/${investigationId}/visual-analysis-runs/${latestVisualRun!.id}/pages`),
    enabled: section === 'visual' && Boolean(latestVisualRun?.id),
    retry: false,
  });
  const enginePage = visualRunPages.data?.items.find((page) => page.page_number === visualPage);
  const activeMapArtifactId = visualLayer === 'localization' ? enginePage?.localization_artifact_id ?? null
    : visualLayer === 'reliability' ? enginePage?.reliability_artifact_id ?? null : null;
  const engineMapImage = useQuery({
    queryKey: ['visual-artifact', investigationId, activeMapArtifactId],
    queryFn: () => apiBlob(`/investigations/${investigationId}/visual-analysis-artifacts/${activeMapArtifactId}`),
    enabled: section === 'visual' && Boolean(activeMapArtifactId),
    retry: false,
  });
  const localizationMapImage = useQuery({
    queryKey: ['visual-artifact', investigationId, enginePage?.localization_artifact_id],
    queryFn: () => apiBlob(`/investigations/${investigationId}/visual-analysis-artifacts/${enginePage!.localization_artifact_id}`),
    enabled: section === 'visual' && visualCompareMode && Boolean(enginePage?.localization_artifact_id), retry: false,
  });
  const reliabilityMapImage = useQuery({
    queryKey: ['visual-artifact', investigationId, enginePage?.reliability_artifact_id],
    queryFn: () => apiBlob(`/investigations/${investigationId}/visual-analysis-artifacts/${enginePage!.reliability_artifact_id}`),
    enabled: section === 'visual' && visualCompareMode && Boolean(enginePage?.reliability_artifact_id), retry: false,
  });
  const engineMapUrl = useMemo(() => engineMapImage.data && typeof URL.createObjectURL === 'function' ? URL.createObjectURL(engineMapImage.data) : '', [engineMapImage.data]);
  useEffect(() => () => { if (engineMapUrl) URL.revokeObjectURL(engineMapUrl); }, [engineMapUrl]);
  const localizationMapUrl = useMemo(() => localizationMapImage.data && typeof URL.createObjectURL === 'function' ? URL.createObjectURL(localizationMapImage.data) : '', [localizationMapImage.data]);
  useEffect(() => () => { if (localizationMapUrl) URL.revokeObjectURL(localizationMapUrl); }, [localizationMapUrl]);
  const reliabilityMapUrl = useMemo(() => reliabilityMapImage.data && typeof URL.createObjectURL === 'function' ? URL.createObjectURL(reliabilityMapImage.data) : '', [reliabilityMapImage.data]);
  useEffect(() => () => { if (reliabilityMapUrl) URL.revokeObjectURL(reliabilityMapUrl); }, [reliabilityMapUrl]);
  const visualOcr = useQuery({
    queryKey: ['visual-ocr', investigationId, visualDocumentId],
    queryFn: () => apiRequest<{ blocks: VisualOcrBlock[]; recognized_text: string }>(`/investigations/${investigationId}/documents/${visualDocumentId}/ocr`),
    enabled: section === 'visual' && Boolean(visualDocument && ['ocr_completed', 'relationship_extraction_pending', 'relationship_extraction_completed', 'relationship_extraction_failed', 'completed'].includes(visualDocument.processing_status)),
  });
  const visualPreview = useQuery({
    queryKey: ['visual-preview', investigationId, visualDocumentId, visualPage],
    queryFn: () => apiBlob(visualDocument?.content_type === 'application/pdf'
      ? `/investigations/${investigationId}/documents/${visualDocumentId}/pages/${visualPage}/preview`
      : `/investigations/${investigationId}/documents/${visualDocumentId}/file`),
    enabled: section === 'visual' && Boolean(visualDocumentId),
    retry: false,
  });
  const visualPreviewUrl = useMemo(() => visualPreview.data && typeof URL.createObjectURL === 'function' ? URL.createObjectURL(visualPreview.data) : '', [visualPreview.data]);
  useEffect(() => () => { if (visualPreviewUrl) URL.revokeObjectURL(visualPreviewUrl); }, [visualPreviewUrl]);
  useEffect(() => { setVisualImageSize({ width: 0, height: 0 }); }, [visualPreviewUrl]);
  useEffect(() => { setVisualPage((page) => Math.min(page, visualPageCount)); }, [visualPageCount]);
  const visualObservations = (latestVisualRun?.result.observations ?? []).filter((observation) => observation.page_number === visualPage);
  const ocrPageGeometry = extractionRun.data?.pages.find((page) => page.page_number === visualPage);
  const localizationAvailable = Boolean(enginePage?.localization_artifact_id);
  const reliabilityAvailable = Boolean(enginePage?.reliability_artifact_id);
  const canCompareMaps = localizationAvailable && reliabilityAvailable;
  const runVisual = useMutation({
    mutationFn: (documentId: string) => apiRequest<VisualRun>(`/investigations/${investigationId}/documents/${documentId}/visual-analysis-runs`, { method: 'POST' }),
    onSuccess: async (run) => {
      setVisualError(run.status === 'failed' ? (run.error_detail || 'Visual analysis failed.') : '');
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ['visual-runs', investigationId] }),
        queryClient.invalidateQueries({ queryKey: ['findings', investigationId] }),
        queryClient.invalidateQueries({ queryKey: ['audit', investigationId] }),
      ]);
    },
    onError: (reason) => setVisualError(reason instanceof ApiError ? reason.message : 'Visual analysis request failed.'),
  });
  const reviewVisualRegion = useMutation({
    mutationFn: ({ id, status, review_notes }: { id: string; status: string; review_notes: string }) => apiRequest<VisualCandidateRegionItem>(`/investigations/${investigationId}/visual-candidate-regions/${id}`, { method: 'PATCH', body: JSON.stringify({ status, review_notes: review_notes || null }) }),
    onSuccess: async () => { await Promise.all([queryClient.invalidateQueries({ queryKey: ['visual-run-pages', investigationId] }), queryClient.invalidateQueries({ queryKey: ['audit', investigationId] })]); },
    onError: (reason) => setVisualError(reason instanceof ApiError ? reason.message : 'Region disposition could not be saved.'),
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
    mutationFn: () => apiRequest<{ candidate_findings_written: number; documents_with_ocr: number; diagnostics: AnalysisDiagnostics }>(`/investigations/${investigationId}/analysis-runs`, { method: 'POST' }),
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
    mutationFn: () => apiRequest<{ id: string; version: number; html: string; content_sha256: string; snapshot: { label: string; ai_narrative: { status: string; text: string | null; reason?: string }; investigator_conclusion: string | null } }>(`/investigations/${investigationId}/dossiers`, { method: 'POST' }),
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

  async function exportDossier(id: string, version: number, format: 'json' | 'html' | 'pdf') {
    const exportKey = `${id}:${format}`;
    setExportingDossier(exportKey);
    try {
      const blob = await apiBlob(`/investigations/${investigationId}/dossiers/${id}/export.${format}`);
      const url = URL.createObjectURL(blob);
      const link = document.createElement('a');
      link.href = url;
      link.download = `dossier-v${version}.${format}`;
      link.click();
      window.setTimeout(() => URL.revokeObjectURL(url), 1000);
      setError('');
      await queryClient.invalidateQueries({ queryKey: ['audit', investigationId] });
    } catch (reason) {
      setError(reason instanceof ApiError ? reason.message : 'The dossier export could not be downloaded.');
    } finally {
      setExportingDossier('');
    }
  }

  function exportComparisons() {
    const comparisons = correlation.data?.comparisons ?? [];
    const rows = [['Canonical field', 'Document', 'Extracted value', 'Status', 'Evidence reference']];
    for (const comparison of comparisons) {
      const status = comparisonStatus(comparison);
      for (const document of visibleComparisonDocuments) {
        const total = comparison.documents.find((item) => item.document_id === document.id)?.total;
        const evidence = total?.evidence_ref;
        rows.push([
          `${comparison.field.replaceAll('_', ' ')} · ${comparison.value}`,
          document.filename,
          total?.raw ?? 'Not available',
          comparisonStatusLabel(status),
          evidence ? `${evidence.filename ?? evidence.document_id ?? ''}${evidence.page_number == null ? '' : ` · page ${evidence.page_number}`}${evidence.block_id ? ` · ${evidence.block_id}` : ''}` : '',
        ]);
      }
    }
    const csv = rows.map((row) => row.map((value) => `"${value.replaceAll('"', '""')}"`).join(',')).join('\r\n');
    const url = URL.createObjectURL(new Blob([csv], { type: 'text/csv;charset=utf-8' }));
    const link = document.createElement('a');
    link.href = url;
    link.download = `${investigationId}-cross-document-comparison.csv`;
    link.click();
    window.setTimeout(() => URL.revokeObjectURL(url), 1000);
  }

  const visualCheckSpecs = [
    { check_type: 'font_baseline', title: 'Font and style inconsistency', Icon: FileText },
    { check_type: 'copy_move', title: 'Repeated visual regions', Icon: ImageIcon },
    { check_type: 'compression_resampling', title: 'Local texture / JPEG block-boundary variation', Icon: AlignLeft },
    { check_type: 'pdf_objects', title: 'Overlay and transparency indicators', Icon: Layers3 },
    { check_type: 'trufor_engine', title: 'TruFor model localization (isolated worker)', Icon: Cpu },
  ];
  const pdfVisualSource = visualDocument?.content_type === 'application/pdf';
  const visualRasterSource = Boolean(visualDocument?.content_type.startsWith('image/'));
  const runVisualButtonLabel = runVisual.isPending && runVisual.variables === visualDocumentId
    ? 'Analyzing source…' : documentVisualHistory.length ? 'Retry analysis' : 'Run visual analysis';

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
          <div className="ocr-block-list">{link.evidence_refs.map((ref, index) => <article key={`${link.id}-${ref.document_id}-${index}`}><b>{ref.filename || ref.document_id}</b><small>{ref.field_type}{ref.page_number == null ? '' : ` · page ${ref.page_number}`}{ref.block_id ? ` · ${ref.block_id}` : ''}</small><p>{ref.quoted_text || 'No source snippet available.'}</p>{ref.document_id && <Link className="text-link" to={evidenceUrl(investigationId, ref)}>Open cited document</Link>}</article>)}</div>
          {link.review_status === 'candidate' && <><label className="auth-label">Review reason<textarea value={correlationReason} onChange={(event) => setCorrelationReason(event.target.value)} maxLength={4000} rows={2} placeholder="Record why this link should be confirmed or rejected"/></label><div className="create-actions"><button className="button secondary small" type="button" disabled={reviewCorrelation.isPending || !correlationReason.trim()} onClick={() => reviewCorrelation.mutate({ id: link.id, status: 'confirmed' })}>Confirm candidate</button><button className="button secondary small" type="button" disabled={reviewCorrelation.isPending || !correlationReason.trim()} onClick={() => reviewCorrelation.mutate({ id: link.id, status: 'rejected' })}>Reject candidate</button></div></>}
        </article>)}
        <section className="panel compare-panel">
          <div className="panel-head comparison-heading">
            <div><h2>Cross-document comparison</h2><p>Values are compared only when evidence is available. Missing values are not counted as mismatches.</p></div>
            <div className="comparison-actions">
              <label className="auth-label">Document
                <select aria-label="Select comparison document" value={comparisonDocumentId} onChange={(event) => setComparisonDocumentId(event.target.value)}>
                  <option value="">All documents</option>
                  {comparisonDocumentItems.map((document) => <option value={document.id} key={document.id}>{document.filename}</option>)}
                </select>
              </label>
              <button className="button secondary small" type="button" disabled={!correlation.data.comparisons.length || comparisonDocuments.isError} onClick={exportComparisons}><Download size={13}/> Export CSV</button>
            </div>
          </div>
          {comparisonDocuments.isLoading ? <p role="status">Loading source documents…</p> : comparisonDocuments.isError ? <p role="alert">Source document names could not be loaded. <button className="text-link" type="button" onClick={() => void comparisonDocuments.refetch()}>Retry</button></p> : correlation.data.comparisons.length === 0 ? <div className="dashboard-empty comparison-empty"><SlidersHorizontal size={22}/><h3>No comparable labeled totals</h3><p>Comparison rows appear when the analysis finds shared identifiers and supported values in source documents.</p></div> : <div className="table-wrap"><table className="dashboard-table compare-table">
            <thead><tr><th>CANONICAL FIELD</th>{visibleComparisonDocuments.map((document) => <th key={document.id}><span>{document.filename}</span><small>{document.processing_status.replaceAll('_', ' ')}</small></th>)}<th>COMPARISON STATUS</th></tr></thead>
            <tbody>{correlation.data.comparisons.map((comparison) => {
              const status = comparisonStatus(comparison);
              return <tr key={`comparison-${comparison.id}`}>
                <td><b>{comparison.field.replaceAll('_', ' ')}</b><small>{comparison.value}</small></td>
                {visibleComparisonDocuments.map((document) => {
                  const source = comparison.documents.find((item) => item.document_id === document.id);
                  const total = source?.total;
                  return <td key={document.id}>{total ? <><Link className={`matrix-cell cell-${status}`} to={evidenceUrl(investigationId, total.evidence_ref)}>{total.raw}</Link><small>{total.evidence_ref.page_number == null ? 'Page unavailable' : `Page ${total.evidence_ref.page_number}`}{total.evidence_ref.block_id ? ` · ${total.evidence_ref.block_id}` : ''}</small></> : <span className="cell-empty">Not available</span>}</td>;
                })}
                <td><span className={`badge ${status === 'consistent' ? 'green' : status === 'difference' ? 'red' : status === 'variation' ? 'amber' : 'neutral'}`}>{comparisonStatusLabel(status)}</span><small>{status === 'not_available' ? 'Insufficient extracted values' : comparison.explanation}</small></td>
              </tr>;
            })}</tbody>
          </table></div>}
        </section>
        {correlation.data.contradictions.map((finding) => <article className="panel" key={finding.id}><div className="panel-head"><div><span className="badge amber">Candidate discrepancy</span><h2>{finding.title}</h2><p>{finding.method}</p></div></div><p>{finding.description}</p><p className="muted">{finding.limitations}</p><div className="ocr-block-list">{finding.evidence_refs.map((ref, index) => <article key={`${finding.id}-${index}`}><b>{ref.filename}</b><small>{ref.field_type}{ref.page_number == null ? '' : ` · page ${ref.page_number}`}{ref.block_id ? ` · ${ref.block_id}` : ''}</small><p>{ref.quoted_text}</p>{ref.document_id && <Link className="text-link" to={evidenceUrl(investigationId, ref)}>Open source</Link>}</article>)}</div></article>)}
        <p className="muted">{correlation.data.limitations} · Results use {correlation.data.method}. Candidate link counts are not confirmed identity matches.</p>
      </>}
    </>}
    {section === 'findings' && <>
      <div className="panel-head"><div><h2>Findings for review</h2><p>Rules create candidates. Accepting one records your review; it does not prove fraud or authenticity.</p></div><div className="head-controls"><label className="auth-label">Status<select aria-label="Filter findings by status" value={statusFilter} onChange={(event) => setStatusFilter(event.target.value)}><option value="">All</option><option value="candidate">Candidate</option><option value="accepted">Accepted</option><option value="needs_review">Needs review</option><option value="rejected">Rejected</option><option value="dismissed">Dismissed</option></select></label>{runButton}</div></div>
      {runAnalysis.data?.diagnostics && <details className="panel" open><summary>Evidence rule diagnostics · {runAnalysis.data.diagnostics.summary.documents_inspected} documents · {runAnalysis.data.diagnostics.summary.findings_produced} findings</summary>
        <p>{runAnalysis.data.diagnostics.data_flow.source}</p><p>{runAnalysis.data.diagnostics.data_flow.structured_extraction_note}</p>
        <div className="table-wrap"><table className="dashboard-table"><thead><tr><th>RULE</th><th>DOCUMENTS</th><th>COMPARISONS</th><th>MATCHED</th><th>SKIPPED</th></tr></thead><tbody>{runAnalysis.data.diagnostics.rules.map((rule) => <tr key={rule.rule_id}><td><details><summary>{rule.rule_id}</summary><p>{rule.description}</p>{rule.comparisons_performed.length > 0 && <pre className="ocr-text">{JSON.stringify(rule.comparisons_performed, null, 2)}</pre>}{rule.rules_skipped.length > 0 && <pre className="ocr-text">{JSON.stringify(rule.rules_skipped, null, 2)}</pre>}</details></td><td>{rule.documents_inspected.length}</td><td>{rule.comparisons_performed.length}</td><td>{rule.rules_matched}</td><td>{rule.rules_skipped.length}</td></tr>)}</tbody></table></div>
        <details><summary>Extracted document fields</summary><pre className="ocr-text">{JSON.stringify(runAnalysis.data.diagnostics.documents, null, 2)}</pre></details>
      </details>}
      {findings.isLoading ? <div className="panel" role="status">Loading findings…</div> : findings.isError ? <div className="panel" role="alert">Findings could not be loaded. <button className="text-link" type="button" onClick={() => void findings.refetch()}>Retry</button></div> : visibleFindings.length === 0 ? <div className="panel dashboard-empty"><FileSearch size={22}/><h2>No findings yet</h2><p>Run evidence rules after OCR finishes. Connections and conflicts appear here only when the stored text supports them.</p>{runButton}</div> : visibleFindings.map((finding) => <article className="panel" key={finding.id}>
        <div className="panel-head"><div><span className={`badge ${tone(finding.severity)}`}>{finding.severity}</span> <span className={`badge ${tone(finding.status)}`}>{finding.status.replaceAll('_', ' ')}</span><h2>{finding.title}</h2><p>{finding.analyzer_name} {finding.analyzer_version} · {finding.method} · engine-style score {finding.confidence.toFixed(2)}, not a calibrated probability</p></div></div>
        <p>{finding.description}</p>
        <p className="muted">{finding.limitations}</p>
        <div className="ocr-block-list">{finding.evidence_refs.map((ref, index) => <article key={`${finding.id}-${index}`}><b>{ref.filename || ref.document_id}</b><small>{ref.field_type || 'source'}{ref.page_number == null ? '' : ` · page ${ref.page_number}`}{ref.block_id ? ` · ${ref.block_id}` : ''}</small><p>{ref.quoted_text || 'No excerpt stored.'}</p>{ref.document_id && <Link className="text-link" to={evidenceUrl(investigationId, ref)}>Open document</Link>}</article>)}</div>
        <label className="auth-label">Review note<textarea value={notes[finding.id] ?? finding.review_notes ?? ''} onChange={(event) => setNotes((current) => ({ ...current, [finding.id]: event.target.value }))} rows={2}/></label>
        <div className="create-actions">
          {(['accepted', 'needs_review', 'rejected', 'dismissed'] as const).map((status) => <button key={status} className="button secondary small" type="button" disabled={review.isPending} onClick={() => review.mutate({ id: finding.id, status, review_notes: notes[finding.id] ?? finding.review_notes ?? '' })}><Check size={13}/> {status.replaceAll('_', ' ')}</button>)}
        </div>
      </article>)}
    </>}
    {section === 'timeline' && <>
      <div className="panel-head"><div><h2>Timeline</h2><p>{timeline.data?.note || 'Dates are taken from OCR text. Ambiguous numeric dates are not silently resolved.'}</p></div>{runButton}</div>
      {timeline.isLoading ? <div className="panel" role="status">Loading timeline…</div> : timeline.isError ? <div className="panel" role="alert">Timeline data could not be loaded. <button type="button" className="text-link" onClick={() => void timeline.refetch()}>Retry</button></div> : orderedTimeline.length === 0 ? <div className="panel dashboard-empty"><Clock3 size={22}/><h2>No dated events</h2><p>Run evidence rules after OCR. Only dates present in recognized text are listed.</p></div> : <ol className="investigation-timeline">{orderedTimeline.map((event, index) => <li className="investigation-timeline-item" key={event.id}><span className="timeline-sequence">{String(index + 1).padStart(2, '0')}</span><span className={`investigation-timeline-marker ${event.ambiguous ? 'is-ambiguous' : ''}`}/><article className="panel"><div className="timeline-event-head"><div><span className="timeline-kind">{event.ambiguous ? 'DATE NEEDS REVIEW' : 'EXTRACTED EVENT'}</span><h3>{event.interpreted_date || event.raw_date}</h3><p>{event.ambiguous ? `Original date text: ${event.raw_date} · date order is ambiguous` : 'Chronological order based on extracted date · timezone not recorded'}</p></div><span className={`badge ${event.ambiguous ? 'amber' : 'blue'}`}>{event.ambiguous ? 'Ambiguous' : 'Interpreted'}</span></div><p className="timeline-event-description">{event.description}</p>{event.evidence_refs[0]?.document_id && <Link className="text-link" to={evidenceUrl(investigationId, event.evidence_refs[0])}>Source: {event.evidence_refs[0].filename || 'document'}{event.evidence_refs[0].page_number == null ? '' : ` · page ${event.evidence_refs[0].page_number}`}{event.evidence_refs[0].block_id ? ` · ${event.evidence_refs[0].block_id}` : ''}</Link>}{event.annotation && <p><b>Investigator note:</b> {event.annotation}</p>}<form className="timeline-note-form" onSubmit={(formEvent) => { formEvent.preventDefault(); const data = new FormData(formEvent.currentTarget); const annotation = String(data.get('annotation') || ''); if (annotation.trim()) annotate.mutate({ id: event.id, annotation }); }}><input className="text-input" name="annotation" aria-label={`Note for ${event.raw_date}`} placeholder="Add an investigator note" defaultValue={event.annotation ?? ''}/><button className="button secondary small" type="submit" disabled={annotate.isPending}>Save note</button></form></article></li>)}</ol>}
    </>}
    {section === 'visual' && <>
      <section className="visual-forensics-shell" aria-labelledby="visual-forensics-title">
        <header className="visual-forensics-header">
          <div><p className="visual-review-eyebrow">Source inspection · investigator review</p><h2 id="visual-forensics-title">Visual forensics</h2><p>Inspect the stored original beside the checks that can run for its file type. Findings remain candidates for review.</p></div>
          <button className="button primary" type="button" disabled={!visualDocument || runVisual.isPending || ['queued', 'processing'].includes(visualDocument.processing_status)} onClick={() => visualDocument && runVisual.mutate(visualDocument.id)}><Eye size={15}/>{runVisualButtonLabel}</button>
        </header>
        {visualError && <div className="auth-error" role="alert">{visualError} <button className="text-link" type="button" onClick={() => setVisualError('')}>Dismiss</button></div>}
        <section className="panel visual-engine-panel" aria-label="TruFor model engine status">
          <div className="panel-head"><div><h3>Model-assisted layer · TruFor</h3><p>RGB + Noiseprint++ fusion (Guillaro et al., CVPR 2023) served by an isolated inference worker. Model outputs are candidate signals, never authenticity verdicts.</p></div><span className={`visual-run-status ${visualEngine.data?.available ? 'status-completed' : 'status-idle'}`}>{visualEngine.data?.available ? 'ENGINE REACHABLE' : 'ENGINE UNAVAILABLE'}</span></div>
          {visualEngine.isLoading ? <p role="status">Checking engine status…</p> : visualEngine.isError ? <p role="alert">Engine status could not be checked. No model output is displayed.</p> : <>
            <ul className="visual-engine-meta">
              <li>Feature flag: <b>{visualEngine.data?.feature_enabled ? 'enabled' : 'disabled (VISUAL_FORENSICS_ENABLED)'}</b></li>
              <li>Availability: <b>{visualEngine.data?.available ? 'worker reachable' : 'no worker configured or reachable'}</b></li>
              <li>Model version: {visualEngine.data?.model_version || 'not reported'}</li>
              <li>Checkpoint SHA-256: {visualEngine.data?.checkpoint_sha256 ? <code>{visualEngine.data.checkpoint_sha256.slice(0, 16)}…</code> : 'not reported'}</li>
              <li>Device: {visualEngine.data?.device || 'not reported'}</li>
            </ul>
            <p className="muted">{visualEngine.data?.reason || 'No TruFor model output exists for this workspace. Nothing here is fabricated; the deterministic checks above remain the only automated analysis.'}</p>
          </>}
          {visualRunPages.data && (visualRunPages.data.items.length ? <div className="visual-engine-pages" aria-label="Engine page results">
            {visualRunPages.data.items.map((page) => <article className="visual-engine-page" key={page.id}>
              <div className="visual-engine-page-head"><b>Page {page.page_number}</b><span className={`badge ${page.status === 'completed' ? 'green' : page.status === 'partial' ? 'amber' : 'red'}`}>{page.status}</span>{page.integrity_score != null && <span className="badge blue" title="Exact official model output; not a calibrated fraud probability">Integrity score (model output) {page.integrity_score.toFixed(3)}</span>}</div>
              <small>{page.engine || 'trufor'} {page.engine_version ? `v${page.engine_version}` : ''} · {page.device || 'device unreported'} · rendered {page.render_width_px ?? '?'}×{page.render_height_px ?? '?'} px at {page.render_dpi ?? '?'} DPI · mapped to {page.source_page_width_points ?? '?'}×{page.source_page_height_points ?? '?'} pt</small>
              <small>{page.localization_artifact_id ? 'Localization map available · select the Localization layer to view it' : 'No localization map for this page'}</small>
              {page.diagnostics?.error_code != null && <p role="status">Page diagnostics: {String(page.diagnostics.error_code)} — {String(page.diagnostics.error_detail ?? '')}</p>}
              {page.page_number === visualPage && page.regions.length > 0 && <div className="table-wrap"><table className="dashboard-table"><thead><tr><th>REGION</th><th>BBOX (PDF points)</th><th>BBOX (pixels)</th><th>MEAN LOCALIZATION</th><th>POST-PROCESSING</th><th>LINKED OCR BLOCKS</th></tr></thead><tbody>{page.regions.map((region) => <tr key={region.id}><td>Candidate {region.region_index + 1}</td><td className="mono">{[region.bbox_pdf_points.x0, region.bbox_pdf_points.y0, region.bbox_pdf_points.x1, region.bbox_pdf_points.y1].map((value) => value.toFixed(1)).join(', ')}</td><td className="mono">{[region.bbox_pixel.x0, region.bbox_pixel.y0, region.bbox_pixel.x1, region.bbox_pixel.y1].map((value) => Math.round(value)).join(', ')}</td><td>{region.mean_localization_score == null ? 'unreported' : region.mean_localization_score.toFixed(3)}</td><td className="mono">{region.postprocess_version}</td><td>{region.linked_ocr_block_ids.length ? region.linked_ocr_block_ids.join(', ') : 'none (navigation link only)'}</td></tr>)}</tbody></table><p className="muted">Region scores are model outputs for candidate review, not evidence of manipulation. Linked OCR blocks are a navigation aid, not proof the text was altered.</p></div>}
            </article>)}
          </div> : <p className="muted">No engine page records exist for the latest run; no model output was produced.</p>)}
        </section>
        {visualDocuments.isLoading ? <div className="visual-forensics-grid"><div className="visual-canvas-skeleton" role="status">Loading source documents…</div><div className="visual-check-panel-skeleton"/></div> : visualDocuments.isError ? <div className="panel" role="alert">Documents could not be loaded. <button type="button" className="text-link" onClick={() => void visualDocuments.refetch()}>Retry</button></div> : (visualDocuments.data?.length ?? 0) === 0 ? <div className="panel visual-empty-state"><FileImage size={23}/><h3>No stored source documents</h3><p>Add a document to this investigation before starting visual analysis.</p><Link className="button secondary small" to={`/investigations/${investigationId}`}>Open investigation documents</Link></div> : <div className="visual-forensics-grid">
          <section className="visual-canvas-panel" aria-label="Source document canvas">
            <div className="visual-canvas-toolbar">
              <div className="visual-canvas-document"><FileText size={15}/><label className="sr-only" htmlFor="visual-document-select">Choose source document</label><select id="visual-document-select" value={visualDocumentId} onChange={(event) => { setVisualDocumentId(event.target.value); setVisualPage(1); setVisualZoom(100); }}>{visualDocuments.data?.map((document) => <option value={document.id} key={document.id}>{document.filename}</option>)}</select></div>
              <label className="visual-layer-select"><Layers3 size={14}/><span className="sr-only">Visible evidence layers</span><select aria-label="Visible evidence layers" value={visualLayer} onChange={(event) => setVisualLayer(event.target.value)}><option value="all">All available layers</option><option value="original">Original only</option><option value="visual">Visual observations</option><option value="ocr">OCR regions</option><option value="localization" disabled={!localizationAvailable}>Localization map (model)</option><option value="reliability" disabled={!reliabilityAvailable}>Reliability map (model)</option></select></label>
              <button type="button" className={`button secondary small ${visualCompareMode ? 'active' : ''}`} disabled={!canCompareMaps} title={canCompareMaps ? 'Compare original, localization, and reliability maps with shared zoom and page navigation' : 'Comparison is available only when both model artifacts exist for this page'} onClick={() => setVisualCompareMode((value) => !value)}>Compare maps</button>
              <div className="visual-canvas-pagination"><button type="button" aria-label="Previous source page" disabled={visualPage <= 1} onClick={() => setVisualPage((page) => Math.max(1, page - 1))}><ChevronLeft size={15}/></button><span>Page {visualPage} of {visualPageCount}</span><button type="button" aria-label="Next source page" disabled={visualPage >= visualPageCount} onClick={() => setVisualPage((page) => Math.min(visualPageCount, page + 1))}><ChevronRight size={15}/></button></div>
            </div>
            <div className="visual-source-meta"><span><i/> {visualDocument?.filename}</span><span>{visualDocument?.id.slice(0, 12)} · {visualDocument?.sha256 ? `${visualDocument.sha256.slice(0, 10)}…` : 'Source hash unavailable'}</span></div>
            <div className={`visual-canvas-viewport ${visualCompareMode ? 'is-pannable' : ''}`} onWheel={(event) => { if (visualCompareMode && event.ctrlKey) { event.preventDefault(); setVisualZoom((zoom) => Math.max(50, Math.min(200, zoom + (event.deltaY < 0 ? 10 : -10)))); } }} onPointerDown={(event) => { if (!visualCompareMode || event.button !== 0) return; event.currentTarget.setPointerCapture(event.pointerId); setPanStart({ x: event.clientX, y: event.clientY, panX: visualPan.x, panY: visualPan.y }); }} onPointerMove={(event) => { if (panStart) setVisualPan({ x: panStart.panX + event.clientX - panStart.x, y: panStart.panY + event.clientY - panStart.y }); }} onPointerUp={() => setPanStart(null)} onPointerCancel={() => setPanStart(null)}>
              {visualPreview.isLoading ? <div className="visual-canvas-skeleton" role="status">Rendering stored source page…</div> : visualPreview.isError ? <div className="visual-canvas-skeleton" role="alert">The stored source preview is unavailable. Analysis can still run against the stored original.</div> : visualPreviewUrl ? <div className={`visual-canvas-page ${visualCompareMode ? 'is-comparison' : ''}`} style={{ width: `${visualZoom}%` }}>
                {visualCompareMode ? <div className="visual-comparison-grid">{([{ kind: 'original', title: 'Original image', src: visualPreviewUrl }, { kind: 'localization', title: 'Localization map', src: localizationMapUrl }, { kind: 'reliability', title: 'Reliability map', src: reliabilityMapUrl }] as const).map(({ kind, title, src }) => <figure key={kind} className="visual-comparison-panel"><figcaption>{title}</figcaption><div className="visual-comparison-image" style={{ transform: `translate(${visualPan.x}px, ${visualPan.y}px) scale(${visualZoom / 100})` }}>{src ? <img src={src} alt={`${title} page ${visualPage}`} style={{ opacity: kind === 'original' ? 1 : visualMapOpacity }} onLoad={(event) => kind === 'original' && setVisualImageSize({ width: event.currentTarget.naturalWidth, height: event.currentTarget.naturalHeight })}/> : <span role="status">{kind === 'localization' ? localizationMapImage.isError ? 'Localization artifact unavailable' : 'Loading localization map…' : kind === 'reliability' ? reliabilityMapImage.isError ? 'Reliability artifact unavailable' : 'Loading reliability map…' : 'Source unavailable'}</span>}</div><small>Page {visualPage} · synchronized zoom {visualZoom}%</small></figure>)}</div> : <>
                <img id="visual-source-image" src={visualPreviewUrl} alt={`Original source ${visualDocument?.filename}, page ${visualPage}`} onLoad={(event) => setVisualImageSize({ width: event.currentTarget.naturalWidth, height: event.currentTarget.naturalHeight })}/>
                {activeMapArtifactId && (engineMapImage.isLoading ? <div className="visual-model-map-loading" role="status">Loading model map artifact…</div> : engineMapImage.isError ? <div className="visual-model-map-error" role="alert">The stored model map artifact could not be loaded. No synthetic map is drawn.</div> : engineMapUrl ? <img className="visual-model-map-overlay" src={engineMapUrl} alt={`${visualLayer === 'localization' ? 'Localization' : 'Reliability'} map (model output) for page ${visualPage}`} style={{ opacity: visualMapOpacity }} aria-label={`${visualLayer} map overlay`}/> : null)}
                {activeMapArtifactId && <p className="visual-model-map-note">Model map display is resized to the page; the stored artifact with its recorded dimensions is authoritative.</p>}
                {(visualLayer === 'all' || visualLayer === 'visual') && visualObservations.flatMap((observation) => observation.regions.map((region, index) => {
                  const pageWidth = enginePage?.source_page_width_points;
                  const pageHeight = enginePage?.source_page_height_points;
                  if (!visualImageSize.width || !visualImageSize.height || !pageWidth || !pageHeight) return null;
                  return <div key={`${observation.finding_id}-${index}`} className="visual-region visual-region-anomaly" title={`${observation.check_type.replaceAll('_', ' ')} · source region ${index + 1}`} style={{ left: `${region.x0 / pageWidth * 100}%`, top: `${region.y0 / pageHeight * 100}%`, width: `${(region.x1 - region.x0) / pageWidth * 100}%`, height: `${(region.y1 - region.y0) / pageHeight * 100}%` }}><span>{observation.check_type.replaceAll('_', ' ')}</span></div>;
                }))}
                {(visualLayer === 'all' || visualLayer === 'ocr') && (visualOcr.data?.blocks ?? []).filter((block) => (block.page_index ?? 0) === visualPage - 1).map((block) => {
                  const rect = visualBounds(block.engine_bbox);
                  const pageWidth = ocrPageGeometry?.width, pageHeight = ocrPageGeometry?.height;
                  if (!rect || !pageWidth || !pageHeight || !visualImageSize.width || !visualImageSize.height) return null;
                  const selected = selectedOcrBlockId === block.block_id;
                  return <button type="button" key={block.block_id} className={`visual-region visual-region-ocr ${selected ? 'is-selected' : ''}`} title={`${block.block_id} · ${block.text}`} onClick={() => setSelectedOcrBlockId(block.block_id)} style={{ left: `${rect.left / pageWidth * 100}%`, top: `${rect.top / pageHeight * 100}%`, width: `${rect.width / pageWidth * 100}%`, height: `${rect.height / pageHeight * 100}%` }}><span>{block.block_id}</span></button>;
                })}
                {(visualLayer === 'all' || visualLayer === 'localization') && (enginePage?.regions ?? []).map((region) => {
                  const box = region.bbox_pdf_points;
                  const pageWidth = enginePage?.source_page_width_points, pageHeight = enginePage?.source_page_height_points;
                  if (!pageWidth || !pageHeight || !visualImageSize.width || !visualImageSize.height) return null;
                  return <button type="button" key={`model-${region.id}`} className={`visual-region visual-region-model ${selectedVisualRegionId === region.id ? 'is-selected' : ''}`} title={`Candidate region ${region.region_index + 1} · mean localization ${region.mean_localization_score ?? 'unreported'} · post-processing ${region.postprocess_version}`} onClick={() => setSelectedVisualRegionId(region.id)} style={{ left: `${box.x0 / pageWidth * 100}%`, top: `${box.y0 / pageHeight * 100}%`, width: `${(box.x1 - box.x0) / pageWidth * 100}%`, height: `${(box.y1 - box.y0) / pageHeight * 100}%` }}><span>candidate {region.region_index + 1}</span></button>;
                })}
              </>}
              </div> : null}
            </div>
            <div className="visual-canvas-footer"><span>{pdfVisualSource ? 'PDF page preview rendered from the stored original' : 'Original image bytes · no synthetic preview'}</span><div className="visual-canvas-footer-controls">{activeMapArtifactId && <label className="visual-map-opacity"><span className="sr-only">Model map opacity</span><input type="range" min={0.1} max={1} step={0.05} value={visualMapOpacity} aria-label="Model map overlay opacity" onChange={(event) => setVisualMapOpacity(Number(event.target.value))}/><span>{Math.round(visualMapOpacity * 100)}%</span></label>}<div className="visual-zoom-controls"><button type="button" aria-label="Zoom out source page" onClick={() => setVisualZoom((zoom) => Math.max(70, zoom - 10))}><ZoomOut size={14}/></button><span>{visualZoom}%</span><button type="button" aria-label="Zoom in source page" onClick={() => setVisualZoom((zoom) => Math.min(150, zoom + 10))}><ZoomIn size={14}/></button><button type="button" onClick={() => setVisualZoom(100)}>Fit</button></div></div></div>
            {selectedVisualRegionId && enginePage?.regions.find((region) => region.id === selectedVisualRegionId) && (() => { const region = enginePage.regions.find((item) => item.id === selectedVisualRegionId)!; return <section className="visual-region-inspector" aria-label="Selected region details"><div className="panel-head"><div><p className="visual-review-eyebrow">Region inspector</p><h3>Candidate {region.region_index + 1} · page {visualPage}</h3></div><span className="badge amber">{region.review_status ?? 'needs_review'}</span></div><p>Localization mean {formatMetric(region.mean_localization_score)} · maximum {formatMetric(region.max_localization_score)} · reliability {formatMetric(region.mean_reliability_value)}</p><small>Pixel bounds: {JSON.stringify(region.bbox_pixel)} · PDF points: {JSON.stringify(region.bbox_pdf_points)}</small><h4>Associated OCR blocks</h4>{region.linked_ocr_block_ids.length ? region.linked_ocr_block_ids.map((blockId) => { const block = visualOcr.data?.blocks.find((item) => item.block_id === blockId); return <div className="visual-linked-ocr" key={blockId}><button className="text-link" type="button" onClick={() => { setSelectedOcrBlockId(blockId); setVisualLayer('ocr'); }}>{blockId}: {block?.text || 'OCR block text unavailable'}</button>{block && <Link to={evidenceUrl(investigationId, { document_id: visualDocumentId, filename: visualDocument?.filename, page_number: visualPage, block_id: blockId })}>Open source evidence</Link>}</div>; }) : <p className="muted">No verified OCR association is stored for this region.</p>}<label className="auth-label">Review note<textarea aria-label="Visual candidate review note" defaultValue={region.review_notes ?? ''} maxLength={4000} rows={2} onChange={(event) => setNotes((current) => ({ ...current, [region.id]: event.target.value }))}/></label><div className="create-actions">{(['accepted', 'needs_review', 'rejected', 'dismissed'] as const).map((status) => <button className="button secondary small" type="button" key={status} disabled={reviewVisualRegion.isPending} onClick={() => reviewVisualRegion.mutate({ id: region.id, status, review_notes: notes[region.id] ?? region.review_notes ?? '' })}>{status.replaceAll('_', ' ')}</button>)}</div></section>; })()}
          </section>
          <aside className="visual-check-panel" aria-label="Visual check availability">
            <div className="visual-check-panel-heading"><div><p className="visual-review-eyebrow">Measured checks</p><h3>Visual check availability</h3><p>{latestVisualRun ? `${latestVisualRun.status} · ${latestVisualRun.result.observations?.length ?? 0} candidate observation(s)` : 'No checks run for this source yet.'}</p></div><span className={`visual-run-status status-${latestVisualRun?.status ?? 'idle'}`}>{latestVisualRun ? latestVisualRun.status : 'NO CHECKS RUN'}</span></div>
            <div className="visual-check-list">{visualCheckSpecs.map(({ check_type, title, Icon }) => {
              const recorded = latestVisualRun?.result.checks?.find((check) => check.check_type === check_type);
              const sourceSupports = check_type === 'trufor_engine' ? pdfVisualSource
                : check_type === 'font_baseline' || check_type === 'pdf_objects' ? pdfVisualSource
                : check_type === 'copy_move' ? Boolean(pdfVisualSource || visualRasterSource)
                  : Boolean(visualRasterSource);
              const unavailableNote = check_type === 'trufor_engine'
                ? 'The isolated TruFor inference worker is not deployed, so no model output exists.'
                : check_type === 'compression_resampling' ? 'This PDF page raster does not isolate source image compression.' : 'This check requires a PDF source.';
              const status = recorded?.status ?? (sourceSupports ? 'not_run' : 'unavailable');
              const statusText = recorded ? status.replaceAll('_', ' ') : sourceSupports ? 'Ready · not run' : 'Unavailable for this source';
              const tone = status === 'executed' ? 'green' : status === 'partial' || status === 'unavailable' ? 'amber' : status === 'failed' ? 'red' : 'blue';
              return <article className="visual-check-item" key={check_type}><span className="visual-check-icon"><Icon size={15}/></span><div className="visual-check-copy"><b>{title}</b><small>{recorded?.algorithm ?? (sourceSupports ? 'Available for this source type.' : unavailableNote)}</small>{recorded?.skipped?.map((skip, index) => <small className="visual-check-reason" key={index}>{skip.reason}</small>)}{recorded?.metrics && <small className="visual-check-metric">{Object.entries(recorded.metrics).filter(([, value]) => typeof value === 'number').slice(0, 3).map(([key, value]) => `${key.replaceAll('_', ' ')} ${value}`).join(' · ')}</small>}</div><span className={`visual-check-badge badge ${tone}`}>{statusText}</span></article>;
            })}</div>
            <div className="visual-observation-summary"><div className="visual-observation-count"><span>Candidate observations</span><b>{latestVisualRun?.result.observations?.length ?? 0}</b></div>{latestVisualRun?.status === 'failed' ? <p role="alert">Analysis failed. {latestVisualRun.error_detail || ''}</p> : !latestVisualRun ? <p>Run checks to measure the stored source. No observation is inferred before analysis.</p> : (latestVisualRun.result.observations?.length ?? 0) > 0 ? <p>Candidate anomalies detected by the implemented checks. Review each source region and its measured evidence before drawing conclusions.</p> : latestVisualRun.status === 'partial' ? <p>Partial coverage: no candidate anomalies were detected by the checks that ran. One or more checks were unavailable for this source.</p> : <p>No candidate anomalies detected by the implemented checks. This does not establish document authenticity.</p>}</div>
            {latestVisualRun?.result.observations?.length ? <div className="visual-observation-list" aria-label="Candidate visual observations">{visualObservations.map((item) => <article key={item.finding_id}><div><b>{item.check_type.replaceAll('_', ' ')} · page {item.page_number}</b><span className="badge blue">{item.review_status}</span></div><p>{item.explanation}</p><small>{item.algorithm}</small><Link className="text-link" to={`/investigations/${investigationId}?doc=${item.document_id}&page=${item.page_number}${item.evidence_ref.block_id ? `&block=${encodeURIComponent(item.evidence_ref.block_id)}` : ''}${item.regions[0] ? `&x0=${item.regions[0].x0}&y0=${item.regions[0].y0}&x1=${item.regions[0].x1}&y1=${item.regions[0].y1}` : ''}`}>Open source region</Link></article>)}</div> : null}
            {selectedOcrBlockId && <article className="visual-selected-ocr"><div className="panel-head"><b>Selected OCR block</b><button className="text-link" type="button" onClick={() => setSelectedOcrBlockId('')}>Clear</button></div><p>{visualOcr.data?.blocks.find((block) => block.block_id === selectedOcrBlockId)?.text ?? 'OCR block unavailable'}</p><Link to={evidenceUrl(investigationId, { document_id: visualDocumentId, filename: visualDocument?.filename, page_number: visualPage, block_id: selectedOcrBlockId })}>Open in evidence viewer</Link></article>}
            <div className="visual-review-boundary"><ShieldAlert size={15}/><span>These checks surface measurable signals for review. They do not determine authenticity, cause, or intent.</span></div>
          </aside>
        </div>}
      </section>
      {visualRuns.isLoading ? <div className="panel" role="status">Loading saved run history…</div> : visualRuns.isError ? <div className="panel" role="alert">Visual run history could not be loaded. <button className="text-link" type="button" onClick={() => void visualRuns.refetch()}>Retry</button></div> : (visualRuns.data?.items.length ?? 0) > 0 && <section className="panel visual-history-panel"><div className="panel-head"><div><h3>Analysis run history</h3><p>Separate runs are retained for audit; retries do not overwrite earlier measurements.</p></div><span>{documentVisualHistory.length} runs for this source</span></div>{documentVisualHistory.map((run, index) => <details className="visual-run-history" key={run.id} open={index === 0}><summary>{run.result.run_kind === 'retry' ? 'Retry run' : run.result.run_kind === 'new' ? 'Initial run' : index === 0 ? 'Latest run' : `Previous run ${documentVisualHistory.length - index}`} · {new Date(run.created_at).toLocaleString()} · {run.status} · {run.result.observations?.length ?? 0} observation(s)</summary><p className="muted">{run.result.page_count ?? 0} pages inspected · {run.started_at ? `started ${new Date(run.started_at).toLocaleTimeString()}` : 'start time unavailable'}{run.completed_at ? ` · completed ${new Date(run.completed_at).toLocaleTimeString()}` : ''}</p>{run.error_detail && <p role="alert">{run.error_detail}</p>}<div className="visual-run-checks">{(run.result.checks ?? []).map((check) => <article key={check.check_type}><b>{check.check_type.replaceAll('_', ' ')}</b><small>{check.status} · {check.pages_analyzed?.length ?? 0} pages · {check.algorithm}</small>{check.metrics && <pre className="ocr-text">{JSON.stringify(check.metrics, null, 2)}</pre>}{check.skipped?.map((skip, index) => <p className="muted" key={index}>Unavailable: {skip.reason}</p>)}</article>)}</div>{run.status === 'failed' ? <p role="alert">Analysis failed; no result was produced.</p> : run.status === 'partial' ? <p className="muted">Partial analysis. Some checks were unavailable for this source type.</p> : (run.result.observations ?? []).length === 0 ? <p className="muted">No candidate anomalies detected by the implemented checks. This does not establish document authenticity.</p> : <div className="visual-history-observations">{run.result.observations?.map((item) => <article key={item.finding_id}><b>{item.check_type.replaceAll('_', ' ')} · page {item.page_number}</b><p>{item.explanation}</p><small>{item.algorithm}</small><pre className="ocr-text">{JSON.stringify(item.metrics, null, 2)}</pre><Link className="text-link" to={`/investigations/${investigationId}?doc=${item.document_id}&page=${item.page_number}${item.evidence_ref.block_id ? `&block=${encodeURIComponent(item.evidence_ref.block_id)}` : ''}${item.regions[0] ? `&x0=${item.regions[0].x0}&y0=${item.regions[0].y0}&x1=${item.regions[0].x1}&y1=${item.regions[0].y1}` : ''}`}>Open source page and region</Link></article>)}</div>}</details>)}</section>}
    </>}
    {section === 'search' && <form className="panel" onSubmit={(event) => { event.preventDefault(); search.mutate(); }}><div className="panel-head"><div><h2>Evidence search</h2><p>Searches stored OCR text and finding titles in this investigation only.</p></div></div><label className="auth-label">Search text<input value={query} onChange={(event) => setQuery(event.target.value)} required placeholder="Invoice number, name, or amount"/></label><button className="button primary" type="submit" disabled={search.isPending}><Search size={14}/> {search.isPending ? 'Searching…' : 'Search evidence'}</button>
      {search.data && <div><p>{search.data.answer}</p><p className="muted">Language model: {search.data.llm.status}. {search.data.llm.reason}</p><div className="ocr-block-list">{search.data.citations.map((citation, index) => <article key={`${citation.kind}-${index}`}><b>{citation.filename || citation.title || citation.kind}</b><small>{citation.page_number == null ? citation.kind : `Page ${citation.page_number}`}</small><p>{citation.quoted_text || citation.status}</p>{citation.document_id && <Link className="text-link" to={`/investigations/${investigationId}?doc=${citation.document_id}`}>Open document</Link>}</article>)}</div></div>}
    </form>}
    {section === 'dossier' && <>
      <div className="panel-head"><div><h2>Investigation dossier</h2><p>Generate an evidence-backed, immutable report snapshot. Candidate findings remain distinct from investigator decisions; every export is audited.</p></div><button className="button primary" type="button" disabled={createDossier.isPending} onClick={() => createDossier.mutate()}><Download size={14}/> {createDossier.isPending ? 'Generating report…' : 'Generate dossier'}</button></div>
      {createDossier.isPending && <div className="panel" role="status">Gathering persisted investigation evidence and preparing the versioned report…</div>}
      {createDossier.isError && <div className="panel" role="alert">Dossier generation failed. {createDossier.error instanceof Error ? createDossier.error.message : 'The saved investigation data could not be assembled.'} <button className="text-link" type="button" onClick={() => createDossier.mutate()}>Retry generation</button></div>}
      {createDossier.isSuccess && createDossier.data && <div className="panel" role="status"><b>Dossier version {createDossier.data.version} saved.</b> Integrity SHA-256: <code>{createDossier.data.content_sha256}</code></div>}
      {createDossier.data && <div className="panel"><div className="panel-head"><div><h3>Version {createDossier.data.version}</h3><p>{createDossier.data.snapshot.label}</p></div><div className="create-actions">{(['json', 'html', 'pdf'] as const).map((format) => <button key={format} className="button secondary small" type="button" disabled={Boolean(exportingDossier)} onClick={() => void exportDossier(createDossier.data!.id, createDossier.data!.version, format)}>{exportingDossier === `${createDossier.data.id}:${format}` ? `Preparing ${format.toUpperCase()}…` : format === 'html' ? 'HTML report' : format.toUpperCase()}</button>)}</div></div><p>Investigator conclusion: {createDossier.data.snapshot.investigator_conclusion || 'Not recorded.'}</p><p>Contextual narrative ({createDossier.data.snapshot.ai_narrative.status}): {createDossier.data.snapshot.ai_narrative.text || createDossier.data.snapshot.ai_narrative.reason || 'Not generated.'}</p><iframe title="Dossier preview" sandbox="" className="dossier-frame" srcDoc={createDossier.data.html}/></div>}
      <section className="panel"><div className="panel-head"><div><h2>Report history</h2><p>All versions are immutable; choose a format to download any saved version.</p></div></div>
      {dossiers.isLoading ? <div role="status">Loading saved reports…</div> : dossiers.isError ? <div role="alert">Report history could not be loaded. <button className="text-link" type="button" onClick={() => void dossiers.refetch()}>Retry</button></div> : (dossiers.data?.items.length ?? 0) === 0 ? <div className="dashboard-empty"><Fingerprint size={22}/><h3>No saved reports yet</h3><p>Generate a dossier to save the first report version.</p></div> : <ul className="activity-list">{dossiers.data?.items.map((item) => <li key={item.id}><span>Version {item.version} · {new Date(item.created_at).toLocaleString()}</span><div className="create-actions">{(['json', 'html', 'pdf'] as const).map((format) => <button key={format} className="button secondary small" type="button" disabled={Boolean(exportingDossier)} onClick={() => void exportDossier(item.id, item.version, format)}>{exportingDossier === `${item.id}:${format}` ? `Preparing ${format.toUpperCase()}…` : format === 'html' ? 'HTML report' : format.toUpperCase()}</button>)}</div></li>)}</ul>}</section>
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

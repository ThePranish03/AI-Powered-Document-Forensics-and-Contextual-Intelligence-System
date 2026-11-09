import { afterEach, describe, expect, it, vi } from 'vitest';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { fireEvent, render, screen } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import InvestigationAnalysis from './InvestigationAnalysis';

afterEach(() => vi.unstubAllGlobals());

describe('visual evidence review', () => {
  it('shows executable checks and the stored-document empty state', async () => {
    vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL) => new Response(JSON.stringify(String(input).endsWith('/documents') ? [] : { items: [] }), {
      status: 200,
      headers: { 'Content-Type': 'application/json' },
    })));
    const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });

    render(
      <QueryClientProvider client={queryClient}>
        <MemoryRouter>
          <InvestigationAnalysis investigationId="case-1" section="visual"/>
        </MemoryRouter>
      </QueryClientProvider>,
    );

    expect(await screen.findByRole('heading', { name: 'Visual evidence review' })).toBeInTheDocument();
    expect(screen.getByText('OCR confidence and identical file hashes are separate signals. Neither is visual analysis, and neither establishes authenticity.')).toBeInTheDocument();
    expect(await screen.findByRole('heading', { name: 'No visual runs yet' })).toBeInTheDocument();
    expect(screen.getAllByText('Source dependent')).toHaveLength(4);
  });

  it('runs analysis for a listed stored document and renders the persisted source-linked candidate', async () => {
    let savedRun: Record<string, unknown> | null = null;
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url.endsWith('/documents')) return new Response(JSON.stringify([{ id: 'doc-7', filename: 'source.pdf', content_type: 'application/pdf', processing_status: 'completed' }]), { status: 200, headers: { 'Content-Type': 'application/json' } });
      if (url.endsWith('/visual-analysis-runs') && init?.method !== 'POST') return new Response(JSON.stringify({ items: savedRun ? [savedRun] : [] }), { status: 200, headers: { 'Content-Type': 'application/json' } });
      if (url.includes('/visual-analysis-runs') && init?.method === 'POST') {
        savedRun = { id: 'run-1', document_id: 'doc-7', status: 'completed', created_at: '2026-11-09T00:00:00Z', result: { checks: [], observations: [{ finding_id: 'finding-1', investigation_id: 'case-1', document_id: 'doc-7', page_number: 1, regions: [{ x0: 10, y0: 20, x1: 40, y1: 60 }], check_type: 'font_baseline', algorithm: 'peer span check', metrics: { font_size_pt: 4 }, explanation: 'Measured source font-size outlier.', limitations: 'Review intentional footnotes.', review_status: 'candidate', evidence_ref: { document_id: 'doc-7', filename: 'source.pdf', page_number: 1, bounding_box: { x0: 10, y0: 20, x1: 40, y1: 60 } } }] } };
        return new Response(JSON.stringify(savedRun), { status: 201, headers: { 'Content-Type': 'application/json' } });
      }
      return new Response(JSON.stringify({ items: [] }), { status: 200, headers: { 'Content-Type': 'application/json' } });
    });
    vi.stubGlobal('fetch', fetchMock);
    const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    render(<QueryClientProvider client={queryClient}><MemoryRouter><InvestigationAnalysis investigationId="case-1" section="visual"/></MemoryRouter></QueryClientProvider>);
    fireEvent.click(await screen.findByRole('button', { name: 'Run visual analysis' }));
    expect(await screen.findByText('Measured source font-size outlier.')).toBeInTheDocument();
    expect(screen.getByRole('link', { name: 'Open source page and region' })).toHaveAttribute('href', expect.stringContaining('x0=10'));
    expect(fetchMock).toHaveBeenCalledWith(expect.stringContaining('/documents/doc-7/visual-analysis-runs'), expect.objectContaining({ method: 'POST' }));
  });

  it('groups retries under one document and distinguishes partial results from no-findings runs', async () => {
    const run = (id: string, date: string) => ({ id, document_id: 'doc-9', status: 'partial', created_at: date, result: { page_count: 1, checks: [{ check_type: 'compression_resampling', status: 'unavailable', algorithm: 'source-image codec check', pages_analyzed: [], metrics: {}, skipped: [{ reason: 'PDF vectors and text cannot be isolated.' }] }], observations: [] } });
    vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url.endsWith('/documents')) return new Response(JSON.stringify([{ id: 'doc-9', filename: 'ledger.pdf', content_type: 'application/pdf', processing_status: 'completed' }]), { status: 200, headers: { 'Content-Type': 'application/json' } });
      if (url.endsWith('/visual-analysis-runs')) return new Response(JSON.stringify({ items: [run('run-2', '2026-11-09T02:00:00Z'), run('run-1', '2026-11-09T01:00:00Z')] }), { status: 200, headers: { 'Content-Type': 'application/json' } });
      return new Response(JSON.stringify({ items: [] }), { status: 200, headers: { 'Content-Type': 'application/json' } });
    }));
    const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    render(<QueryClientProvider client={queryClient}><MemoryRouter><InvestigationAnalysis investigationId="case-1" section="visual"/></MemoryRouter></QueryClientProvider>);
    expect(await screen.findByText('Analysis run history · 2 saved runs')).toBeInTheDocument();
    expect(screen.getByText(/Previous run 1/)).toBeInTheDocument();
    expect(screen.getAllByText(/Partial analysis: one or more checks were unavailable/)).toHaveLength(2);
    expect(screen.getAllByText(/Unavailable\/skipped: PDF vectors and text cannot be isolated/)).toHaveLength(2);
    expect(screen.getByRole('button', { name: 'Retry analysis' })).toBeEnabled();
  });
});
describe('cross-document intelligence', () => {
  it('renders API-backed identifier links and source citations', async () => {
    let reviewed = false;
    const fetchMock = vi.fn(async (_input: RequestInfo | URL, init?: RequestInit) => {
      if (init?.method === 'PATCH') {
        reviewed = true;
        return new Response(JSON.stringify({ id: 'link-1', review_status: 'confirmed', reviewed_at: '2026-01-01T00:00:00Z', review_reason: 'Checked the citations.' }), { status: 200, headers: { 'Content-Type': 'application/json' } });
      }
      const reviewStatus = reviewed ? 'confirmed' : 'candidate';
      return new Response(JSON.stringify({
      status: 'ready', method: 'deterministic_exact_identifier_v1',
      summary: { documents_total: 2, documents_with_ocr: 2, documents_pending: 0, documents_with_processing_errors: 0, identifier_mentions: 2, candidate_entities: 1, total_links: 1, candidate_links: reviewed ? 0 : 1, confirmed_links: reviewed ? 1 : 0, rejected_links: 0, unreviewed_links: reviewed ? 0 : 1, contradictions: 0 },
      links: [{ id: 'link-1', relation_type: 'shared_document_identifier', normalized_identifier: 'INV-10001', identifier_type: 'INVOICE', document_ids: ['doc-1', 'doc-2'], document_count: 2, rank: 1, rank_kind: 'exact_rule_match', method: 'exact_identifier_v1', explanation: 'Exact source identifier match.', review_status: reviewStatus, review_reason: reviewed ? 'Checked the citations.' : null, evidence_refs: [{ document_id: 'doc-1', filename: 'invoice-a.pdf', page_number: 1, block_id: 'b1', field_type: 'document_identifier', quoted_text: 'Invoice No: INV-10001' }] }],
      comparisons: [], contradictions: [], limitations: 'Heuristic leads only.',
    }), { status: 200, headers: { 'Content-Type': 'application/json' } });
    });
    vi.stubGlobal('fetch', fetchMock);
    const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    render(<QueryClientProvider client={queryClient}><MemoryRouter><InvestigationAnalysis investigationId="case-1" section="correlation"/></MemoryRouter></QueryClientProvider>);
    expect(await screen.findByRole('heading', { name: 'INV-10001' })).toBeInTheDocument();
    expect(screen.getByText('Invoice No: INV-10001')).toBeInTheDocument();
    expect(screen.getByRole('link', { name: 'Open cited document' })).toHaveAttribute('href', '/investigations/case-1?doc=doc-1&page=1&block=b1');
    expect(screen.getByRole('button', { name: /re-run correlation/i })).toBeEnabled();
    fireEvent.change(screen.getByLabelText('Review reason'), { target: { value: 'Checked the citations.' } });
    fireEvent.click(screen.getByRole('button', { name: 'Confirm candidate' }));
    expect(await screen.findByText(/review note: checked the citations/i)).toBeInTheDocument();
    expect(fetchMock).toHaveBeenCalledWith(expect.stringContaining('/correlation/link-1/review'), expect.objectContaining({ method: 'PATCH', body: JSON.stringify({ status: 'confirmed', reason: 'Checked the citations.' }) }));
  });

  it('keeps missing values out of the mismatch status and links values to evidence', async () => {
    vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url.endsWith('/documents')) return new Response(JSON.stringify([
        { id: 'doc-1', filename: 'invoice-a.pdf', content_type: 'application/pdf', processing_status: 'completed' },
        { id: 'doc-2', filename: 'invoice-b.pdf', content_type: 'application/pdf', processing_status: 'completed' },
        { id: 'doc-3', filename: 'invoice-c.pdf', content_type: 'application/pdf', processing_status: 'completed' },
      ]), { status: 200, headers: { 'Content-Type': 'application/json' } });
      return new Response(JSON.stringify({
        status: 'ready',
        method: 'deterministic_exact_identifier_v1',
        summary: { documents_total: 3, documents_with_ocr: 3, documents_pending: 0, documents_with_processing_errors: 0, identifier_mentions: 3, candidate_entities: 1, total_links: 1, candidate_links: 1, confirmed_links: 0, rejected_links: 0, unreviewed_links: 1, contradictions: 0 },
        links: [],
        comparisons: [{
          id: 'comparison-1',
          field: 'document_identifier',
          value: 'INV-10001',
          status: 'matched_identifier',
          explanation: 'Shared identifier; missing values are not treated as agreement.',
          documents: [
            { document_id: 'doc-1', filename: 'invoice-a.pdf', total: { raw: '100.00', evidence_ref: { document_id: 'doc-1', filename: 'invoice-a.pdf', page_number: 1, block_id: 'block-1' } }, dates: [] },
            { document_id: 'doc-2', filename: 'invoice-b.pdf', total: null, dates: [] },
            { document_id: 'doc-3', filename: 'invoice-c.pdf', total: { raw: '100.00', evidence_ref: { document_id: 'doc-3', filename: 'invoice-c.pdf', page_number: 2, block_id: 'block-3' } }, dates: [] },
          ],
        }],
        contradictions: [],
        limitations: 'Heuristic leads only.',
      }), { status: 200, headers: { 'Content-Type': 'application/json' } });
    }));
    const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });

    render(<QueryClientProvider client={queryClient}><MemoryRouter><InvestigationAnalysis investigationId="case-1" section="correlation"/></MemoryRouter></QueryClientProvider>);

    expect(await screen.findByRole('heading', { name: 'Cross-document comparison' })).toBeInTheDocument();
    expect(await screen.findByText('Consistent')).toBeInTheDocument();
    expect(screen.getByText('Not available')).toBeInTheDocument();
    expect(screen.getAllByRole('link', { name: '100.00' }).map((link) => link.getAttribute('href'))).toEqual([
      '/investigations/case-1?doc=doc-1&page=1&block=block-1',
      '/investigations/case-1?doc=doc-3&page=2&block=block-3',
    ]);
    expect(screen.getByRole('button', { name: 'Export CSV' })).toBeEnabled();
  });
});

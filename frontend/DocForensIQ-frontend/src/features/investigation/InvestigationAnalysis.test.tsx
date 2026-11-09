import { afterEach, describe, expect, it, vi } from 'vitest';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { fireEvent, render, screen } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import InvestigationAnalysis from './InvestigationAnalysis';

afterEach(() => vi.unstubAllGlobals());

describe('visual evidence review', () => {
  it('distinguishes manual source review from unavailable automated checks', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => new Response(JSON.stringify({ items: [] }), {
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
    expect(screen.getByRole('link', { name: 'Open document review' })).toHaveAttribute('href', '/investigations/case-1');
    expect(screen.getAllByText('Not implemented')).toHaveLength(4);
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
    expect(screen.getByRole('link', { name: 'Open cited document' })).toHaveAttribute('href', '/investigations/case-1?doc=doc-1');
    expect(screen.getByRole('button', { name: /re-run correlation/i })).toBeEnabled();
    fireEvent.change(screen.getByLabelText('Review reason'), { target: { value: 'Checked the citations.' } });
    fireEvent.click(screen.getByRole('button', { name: 'Confirm candidate' }));
    expect(await screen.findByText(/review note: checked the citations/i)).toBeInTheDocument();
    expect(fetchMock).toHaveBeenCalledWith(expect.stringContaining('/correlation/link-1/review'), expect.objectContaining({ method: 'PATCH', body: JSON.stringify({ status: 'confirmed', reason: 'Checked the citations.' }) }));
  });
});

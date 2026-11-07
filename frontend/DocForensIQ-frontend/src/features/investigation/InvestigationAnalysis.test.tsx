import { afterEach, describe, expect, it, vi } from 'vitest';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen } from '@testing-library/react';
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

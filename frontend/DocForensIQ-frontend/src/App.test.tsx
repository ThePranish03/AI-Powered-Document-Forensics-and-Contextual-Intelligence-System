import { afterEach, describe, expect, it, vi } from 'vitest';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { fireEvent, render, screen, within } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import App, { LegacyWorkstation } from './App';
import { AuthProvider } from './auth/AuthProvider';

const user = {
  id: 'user-1',
  full_name: 'Avery Investigator',
  email: 'avery@example.test',
  role: 'investigator',
  permissions: ['investigations:read', 'investigations:create', 'investigations:update'],
};

function jsonResponse(status: number, body?: unknown) {
  return new Response(body === undefined ? null : JSON.stringify(body), {
    status,
    headers: body === undefined ? undefined : { 'Content-Type': 'application/json' },
  });
}

function mockFetch(handler: (url: string, method: string) => Response) {
  vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => handler(String(input), init?.method || 'GET')));
}

function renderAt(path: string) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(<QueryClientProvider client={client}><MemoryRouter initialEntries={[path]}><App/></MemoryRouter></QueryClientProvider>);
}

function renderLegacyAt(path: string) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(<QueryClientProvider client={client}><MemoryRouter initialEntries={[path]}><AuthProvider><LegacyWorkstation/></AuthProvider></MemoryRouter></QueryClientProvider>);
}

afterEach(() => vi.unstubAllGlobals());

describe('DocForensIQ authentication and workspace', () => {
  it('shows the public landing page to signed-out visitors', async () => {
    mockFetch(() => jsonResponse(401, { detail: 'Authentication required' }));
    renderAt('/');
    expect(await screen.findByRole('heading', { name: /beyond ocr/i })).toBeInTheDocument();
    expect(screen.getByRole('link', { name: /start investigation/i })).toHaveAttribute('href', '/investigations/new');
    expect(screen.getByRole('link', { name: 'Sign in' })).toHaveAttribute('href', '/login');
    expect(screen.getByRole('img', { name: /evidence viewer preview/i })).toBeInTheDocument();
  });

  it('redirects protected workspace routes to login', async () => {
    mockFetch(() => jsonResponse(401, { detail: 'Authentication required' }));
    renderAt('/investigations');
    expect(await screen.findByRole('heading', { name: /welcome back/i })).toBeInTheDocument();
  });

  it('shows an API error for invalid login instead of a fake success', async () => {
    mockFetch((url, method) => url.endsWith('/auth/login') && method === 'POST'
      ? jsonResponse(401, { detail: 'Email or password is incorrect' })
      : jsonResponse(401, { detail: 'Authentication required' }));
    renderAt('/login');
    fireEvent.change(screen.getByLabelText('Email address'), { target: { value: 'bad@example.test' } });
    fireEvent.change(screen.getByLabelText('Password'), { target: { value: 'wrong-password' } });
    fireEvent.click(screen.getByRole('button', { name: /sign in/i }));
    expect(await screen.findByRole('alert')).toHaveTextContent(/email or password is incorrect/i);
  });

  it('restores a session and renders only returned dashboard values', async () => {
    mockFetch((url) => {
      if (url.endsWith('/auth/me')) return jsonResponse(200, user);
      if (url.endsWith('/dashboard/summary')) return jsonResponse(200, { total_investigations: 0, active_investigations: 0, in_review_investigations: 0, completed_investigations: 0 });
      if (url.includes('/dashboard/recent-investigations')) return jsonResponse(200, []);
      if (url.includes('/dashboard/activity')) return jsonResponse(200, []);
      return jsonResponse(404, { detail: 'Not found' });
    });
    renderAt('/');
    expect(await screen.findByRole('heading', { name: /good to have you back, avery/i })).toBeInTheDocument();
    expect(await screen.findByRole('heading', { name: /your workspace is ready/i })).toBeInTheDocument();
    expect(screen.getByText('0', { selector: 'strong' })).toBeInTheDocument();
    expect(screen.getAllByText('—', { selector: 'strong' })).toHaveLength(3);
    expect(screen.getByRole('heading', { name: /beyond ocr.*evidence-led document intelligence/i })).toBeInTheDocument();
    expect(screen.getByRole('link', { name: /visual forensics/i })).toHaveAttribute('href', '/investigations/new');
  });

  it('redirects successful login to the originally requested protected route', async () => {
    mockFetch((url, method) => {
      if (url.endsWith('/auth/login') && method === 'POST') return jsonResponse(200, user);
      if (url.includes('/investigations?')) return jsonResponse(200, { items: [], offset: 0, limit: 100 });
      if (url.includes('/dashboard/recent-investigations')) return jsonResponse(200, []);
      return jsonResponse(401, { detail: 'Authentication required' });
    });
    renderAt('/investigations');
    fireEvent.change(await screen.findByLabelText('Email address'), { target: { value: user.email } });
    fireEvent.change(screen.getByLabelText('Password'), { target: { value: 'StrongPassword123' } });
    fireEvent.click(screen.getByRole('button', { name: /sign in/i }));
    expect(await screen.findByRole('heading', { name: 'Investigations' })).toBeInTheDocument();
    expect(await screen.findByRole('heading', { name: /no investigations found/i })).toBeInTheDocument();
  });

  it('creates an investigation through the API and refreshes the list queries', async () => {
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url.endsWith('/auth/me')) return jsonResponse(200, user);
      if (url.endsWith('/investigations') && init?.method === 'POST') return jsonResponse(201, {
        id: 'case-123', case_number: 'DF-12345678', title: 'Invoice review', status: 'draft', document_count: 0, updated_at: '2026-01-01T00:00:00Z',
      });
      if (url.endsWith('/investigations/case-123')) return jsonResponse(200, {
        id: 'case-123', case_number: 'DF-12345678', title: 'Invoice review', status: 'draft', document_count: 0, updated_at: '2026-01-01T00:00:00Z',
      });
      return jsonResponse(200, { items: [] });
    });
    vi.stubGlobal('fetch', fetchMock);
    renderAt('/investigations/new');
    fireEvent.change(await screen.findByLabelText('Investigation title'), { target: { value: 'Invoice review' } });
    fireEvent.click(screen.getByRole('button', { name: /create investigation/i }));
    expect(await screen.findByRole('heading', { name: 'Invoice review' })).toBeInTheDocument();
    expect(fetchMock).toHaveBeenCalledWith(expect.stringContaining('/api/v1/investigations'), expect.objectContaining({ method: 'POST', credentials: 'include' }));
  });

  it('loads OCR text and source block details for a selected document', async () => {
    mockFetch((url) => {
      if (url.endsWith('/auth/me')) return jsonResponse(200, user);
      if (url.endsWith('/investigations/case-123')) return jsonResponse(200, {
        id: 'case-123', case_number: 'DF-12345678', title: 'Invoice review', status: 'draft', document_count: 1, updated_at: '2026-01-01T00:00:00Z',
      });
      if (url.endsWith('/documents')) return jsonResponse(200, [{ id: 'doc-1', filename: 'invoice.png', size_bytes: 1024, processing_status: 'ocr_completed', attempts_remaining: 3, created_at: '2026-01-01T00:00:00Z' }]);
      if (url.endsWith('/doc-1/ocr')) return jsonResponse(200, {
        document_id: 'doc-1', provider: 'paddleocr_ppstructurev3', provider_version: '3.3.2', recognized_text: 'ACME Ltd',
        blocks: [{ block_id: 'block_000001', source_path: 'root[0].blocks[0]', text: 'ACME Ltd', page_index: 0, engine_bbox: [1, 2, 3, 4], engine_confidence: 0.91 }],
      });
      return jsonResponse(404, { detail: 'Not found' });
    });
    renderAt('/investigations/case-123');
    fireEvent.click(await screen.findByRole('button', { name: 'invoice.png' }));
    expect(await screen.findByRole('heading', { name: 'Recognized text' })).toBeInTheDocument();
    expect(screen.getAllByText('ACME Ltd')).toHaveLength(2);
    expect(screen.getByText('block_000001')).toBeInTheDocument();
  });

  it('uploads multiple documents and reports a partial failure', async () => {
    let uploadCount = 0;
    mockFetch((url, method) => {
      if (url.endsWith('/auth/me')) return jsonResponse(200, user);
      if (url.endsWith('/investigations/case-123')) return jsonResponse(200, {
        id: 'case-123', case_number: 'DF-12345678', title: 'Invoice review', status: 'draft', document_count: 0, updated_at: '2026-01-01T00:00:00Z',
      });
      if (url.endsWith('/documents') && method === 'POST') {
        uploadCount += 1;
        return uploadCount === 1
          ? jsonResponse(201, { id: 'doc-1', filename: 'first.png' })
          : jsonResponse(415, { detail: 'Unsupported document' });
      }
      if (url.endsWith('/documents')) return jsonResponse(200, []);
      return jsonResponse(404, { detail: 'Not found' });
    });
    renderAt('/investigations/case-123');
    const files = [
      new File(['first'], 'first.png', { type: 'image/png' }),
      new File(['second'], 'second.png', { type: 'image/png' }),
    ];
    fireEvent.change(await screen.findByLabelText('Choose document files'), { target: { files } });
    // jsdom validates the required file input against its internal FileList, which the
    // shadowed `files` property does not update, so submit the form directly.
    fireEvent.submit(screen.getByRole('button', { name: /upload and start ocr/i }).closest('form')!);
    expect(await screen.findByText('1 of 2 file(s) uploaded. OCR starts automatically for each accepted document.')).toBeInTheDocument();
    expect(await screen.findByRole('alert')).toHaveTextContent('second.png: Unsupported document');
    expect(uploadCount).toBe(2);
  });

  it('uploads a file dropped onto the source panel', async () => {
    let uploadCount = 0;
    mockFetch((url, method) => {
      if (url.endsWith('/auth/me')) return jsonResponse(200, user);
      if (url.endsWith('/investigations/case-123')) return jsonResponse(200, {
        id: 'case-123', case_number: 'DF-12345678', title: 'Invoice review', status: 'draft', document_count: 0, updated_at: '2026-01-01T00:00:00Z',
      });
      if (url.endsWith('/documents') && method === 'POST') {
        uploadCount += 1;
        return jsonResponse(201, { id: 'doc-drop', filename: 'dropped.png' });
      }
      if (url.endsWith('/documents')) return jsonResponse(200, []);
      return jsonResponse(404, { detail: 'Not found' });
    });
    renderAt('/investigations/case-123');
    const form = (await screen.findByRole('button', { name: /upload and start ocr/i })).closest('form')!;
    const file = new File(['dropped'], 'dropped.png', { type: 'image/png' });
    fireEvent.drop(form, { dataTransfer: { files: [file] } });
    expect(await screen.findByText('1 of 1 file(s) uploaded. OCR starts automatically for each accepted document.')).toBeInTheDocument();
    expect(uploadCount).toBe(1);
  });

  it('searches persisted relationship entities and opens their source details', async () => {
    mockFetch((url) => {
      if (url.endsWith('/auth/me')) return jsonResponse(200, user);
      if (url.endsWith('/investigations/case-123')) return jsonResponse(200, {
        id: 'case-123', case_number: 'DF-12345678', title: 'Invoice review', status: 'draft', document_count: 0, updated_at: '2026-01-01T00:00:00Z',
      });
      if (url.endsWith('/documents')) return jsonResponse(200, [{
        id: 'doc-1', filename: 'invoice.png', content_type: 'image/png', size_bytes: 2048,
        processing_status: 'ocr_completed', attempts_remaining: 3, created_at: '2026-01-01T00:00:00Z',
        sha256: 'a'.repeat(64),
      }]);
      if (url.endsWith('/relationships/graph')) return jsonResponse(200, {
        nodes: [{ id: 'entity-1', label: 'Acme Ltd', type: 'ORGANIZATION', document_ids: ['doc-1'], resolution: 'unresolved' }], edges: [], meta: { investigation_id: 'case-123', graph_version: 'graph_v1' },
      });
      if (url.includes('/relationships/search')) return jsonResponse(200, { items: [{ id: 'entity-1', label: 'Acme Ltd', type: 'ORGANIZATION', document_ids: ['doc-1'], resolution: 'unresolved' }] });
      if (url.endsWith('/relationships/entities/entity-1')) return jsonResponse(200, { node: { id: 'entity-1', label: 'Acme Ltd', type: 'ORGANIZATION', document_ids: ['doc-1'], resolution: 'unresolved' }, relationships: [] });
      return jsonResponse(404, { detail: 'Not found' });
    });
    renderAt('/investigations/case-123/relationships');
    fireEvent.change(await screen.findByRole('textbox', { name: /search entities/i }), { target: { value: 'Acme' } });
    expect(await screen.findByText('ENTITY DETAILS')).toBeInTheDocument();
    expect(screen.getByRole('heading', { name: 'Acme Ltd' })).toBeInTheDocument();
    fireEvent.click(await screen.findByRole('button', { name: /mentioned in invoice\.png/i }));
    expect(await screen.findByRole('button', { name: 'invoice.png' })).toBeInTheDocument();
  });

  it('shows relationship claims in the table view with source details', async () => {
    mockFetch((url) => {
      if (url.endsWith('/auth/me')) return jsonResponse(200, user);
      if (url.endsWith('/investigations/case-123')) return jsonResponse(200, {
        id: 'case-123', case_number: 'DF-12345678', title: 'Invoice review', status: 'draft', document_count: 0, updated_at: '2026-01-01T00:00:00Z',
      });
      if (url.endsWith('/documents')) return jsonResponse(200, [{
        id: 'doc-1', filename: 'invoice.png', content_type: 'image/png', size_bytes: 2048,
        processing_status: 'ocr_completed', attempts_remaining: 3, created_at: '2026-01-01T00:00:00Z',
        sha256: 'a'.repeat(64),
      }]);
      if (url.endsWith('/relationships/graph')) return jsonResponse(200, {
        nodes: [{ id: 'entity-1', label: 'Acme Ltd', type: 'ORGANIZATION', document_ids: ['doc-1'], resolution: 'unresolved' }], edges: [], meta: { investigation_id: 'case-123', graph_version: 'graph_v1' },
      });
      return jsonResponse(404, { detail: 'Not found' });
    });
    renderAt('/investigations/case-123/relationships');
    fireEvent.click(await screen.findByRole('button', { name: 'Table' }));
    fireEvent.change(await screen.findByRole('textbox', { name: /search documents, entities, or source blocks/i }), { target: { value: 'Acme' } });
    fireEvent.click(await screen.findByRole('button', { name: 'Acme Ltd' }));
    expect(await screen.findByText('EXTRACTED ENTITY')).toBeInTheDocument();
    expect(within(screen.getByLabelText('Selected graph item details')).getByText('Entity type')).toBeInTheDocument();
  });

  it('logs out from the account menu', async () => {
    mockFetch((url, method) => {
      if (url.endsWith('/auth/me')) return jsonResponse(200, user);
      if (url.endsWith('/auth/logout') && method === 'POST') return jsonResponse(204);
      if (url.includes('/dashboard/summary')) return jsonResponse(200, { total_investigations: 0, active_investigations: 0, in_review_investigations: 0, completed_investigations: 0 });
      if (url.includes('/dashboard/recent-investigations') || url.includes('/dashboard/activity')) return jsonResponse(200, []);
      return jsonResponse(401, { detail: 'Authentication required' });
    });
    renderAt('/');
    fireEvent.click(await screen.findByRole('button', { name: /avery investigator/i }));
    fireEvent.click(screen.getByRole('button', { name: /sign out/i }));
    expect(await screen.findByRole('heading', { name: /welcome back/i })).toBeInTheDocument();
  });
});

describe('Legacy synthetic workstation', () => {
  it('labels synthetic landing content as demonstration data', async () => {
    mockFetch((url) => url.endsWith('/auth/me') ? jsonResponse(200, user) : jsonResponse(200, true));
    renderLegacyAt('/');
    expect(await screen.findByRole('heading', { name: /evidence-led document intelligence/i })).toBeInTheDocument();
    expect(screen.getByText(/Demo data is synthetic/i)).toBeInTheDocument();
  });

  it('keeps the existing evidence viewer review controls available in demo mode', async () => {
    mockFetch((url) => url.endsWith('/auth/me') ? jsonResponse(200, user) : jsonResponse(200, true));
    renderLegacyAt('/investigations/DF-2024-00421/evidence');
    expect(await screen.findByRole('heading', { name: /evidence viewer/i })).toBeInTheDocument();
    expect(screen.getAllByText(/Detected inconsistency/).length).toBeGreaterThan(0);
    expect(screen.getByRole('button', { name: /accept \/ verify/i })).toBeInTheDocument();
    expect(screen.queryByText(/fraud confirmed/i)).not.toBeInTheDocument();
  });

  it('requires a rationale before changing a demo finding review state', async () => {
    mockFetch((url) => url.endsWith('/auth/me') ? jsonResponse(200, user) : jsonResponse(200, true));
    renderLegacyAt('/investigations/DF-2024-00421/findings');
    expect(await screen.findByRole('heading', { name: /analysis findings/i })).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: /accept \/ verify/i }));
    expect(screen.getByLabelText(/review rationale/i)).toBeInTheDocument();
  });
});

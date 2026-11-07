export class ApiError extends Error {
  constructor(public readonly status: number, message: string) {
    super(message);
    this.name = 'ApiError';
  }
}

const API_BASE_URL = (import.meta.env.VITE_API_BASE_URL || 'http://localhost:8000').replace(/\/$/, '');
const NO_REFRESH_PATHS = ['/auth/login', '/auth/register', '/auth/refresh', '/auth/password-reset'];
let refreshRequest: Promise<Response> | null = null;

function csrfToken(): string | undefined {
  const entry = document.cookie.split('; ').find((item) => item.startsWith('dfi_csrf='));
  return entry ? decodeURIComponent(entry.slice('dfi_csrf='.length)) : undefined;
}

async function send(path: string, init: RequestInit = {}): Promise<Response> {
  const headers = new Headers(init.headers);
  if (typeof init.body === 'string' && !headers.has('Content-Type')) headers.set('Content-Type', 'application/json');
  const token = csrfToken();
  if (token && !['GET', 'HEAD', 'OPTIONS'].includes((init.method || 'GET').toUpperCase())) {
    headers.set('X-CSRF-Token', token);
  }
  return fetch(`${API_BASE_URL}/api/v1${path}`, {
    ...init,
    headers,
    credentials: 'include',
  });
}

function refreshSession(): Promise<Response> {
  if (!refreshRequest) {
    refreshRequest = send('/auth/refresh', { method: 'POST' }).finally(() => { refreshRequest = null; });
  }
  return refreshRequest;
}

async function readError(response: Response): Promise<ApiError> {
  let message = 'The request could not be completed. Please try again.';
  try {
    const body: unknown = await response.json();
    if (typeof body === 'object' && body !== null && 'detail' in body && typeof body.detail === 'string') {
      message = body.detail;
    }
  } catch {
    // Keep the user-facing message generic when the server does not return JSON.
  }
  return new ApiError(response.status, message);
}

export async function apiRequest<T>(path: string, init: RequestInit = {}): Promise<T> {
  let response = await send(path, init);
  if (response.status === 401 && csrfToken() && !NO_REFRESH_PATHS.some((part) => path.startsWith(part))) {
    const refreshed = await refreshSession();
    if (refreshed.ok) response = await send(path, init);
  }
  if (response.status === 401 && !NO_REFRESH_PATHS.some((part) => path.startsWith(part))) {
    window.dispatchEvent(new Event('docforensiq:unauthorized'));
  }
  if (!response.ok) throw await readError(response);
  if (response.status === 204) return undefined as T;
  return response.json() as Promise<T>;
}

export async function apiBlob(path: string): Promise<Blob> {
  let response = await send(path);
  if (response.status === 401 && csrfToken()) {
    const refreshed = await refreshSession();
    if (refreshed.ok) response = await send(path);
  }
  if (response.status === 401) window.dispatchEvent(new Event('docforensiq:unauthorized'));
  if (!response.ok) throw await readError(response);
  return response.blob();
}

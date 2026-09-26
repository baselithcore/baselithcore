export const API_BASE = '/baselithbot';
export const DASH = `${API_BASE}/dash`;
// Exported for tests only; not part of the intended public surface.
export const DASHBOARD_TOKEN_STORAGE_KEY = 'baselithbot.dashboard.token';

// The dashboard token is read from sessionStorage ONLY and sent as an
// `Authorization: Bearer` header. It must never be appended to a URL as a
// `?token=` query parameter: that would leak it into access logs, browser
// history and Referer headers (see plugins/baselithbot/policies/dashboard_auth.py,
// which never accepts a query-string token for regular requests — only the
// SSE stream ticket flow uses a short-lived, single-use query value).
function getDashboardToken(): string | null {
  if (typeof window === 'undefined') return null;
  try {
    const stored = window.sessionStorage.getItem(DASHBOARD_TOKEN_STORAGE_KEY)?.trim();
    return stored || null;
  } catch {
    return null;
  }
}

export class ApiError extends Error {
  status: number;
  body: unknown;
  constructor(status: number, message: string, body: unknown) {
    super(message);
    this.status = status;
    this.body = body;
  }
}

export async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  const token = getDashboardToken();
  // `headers` is merged, not spread last: `...init` after the merged object
  // replaced it wholesale whenever a caller passed its own headers, silently
  // dropping the Authorization header. `credentials` is pinned for the same
  // reason — no caller may widen it to 'include'.
  const { headers: extraHeaders, ...rest } = init;
  const headers = new Headers(extraHeaders);
  if (!headers.has('Content-Type')) headers.set('Content-Type', 'application/json');
  if (!headers.has('Accept')) headers.set('Accept', 'application/json');
  if (token) headers.set('Authorization', `Bearer ${token}`);
  const res = await fetch(path, {
    ...rest,
    headers,
    credentials: 'same-origin',
  });
  const raw = await res.text();
  let body: unknown = raw;
  try {
    body = raw ? JSON.parse(raw) : null;
  } catch {
    /* keep as text */
  }
  if (!res.ok) {
    const detail =
      (body && typeof body === 'object' && 'detail' in body
        ? String((body as { detail?: unknown }).detail)
        : res.statusText) || `HTTP ${res.status}`;
    throw new ApiError(res.status, detail, body);
  }
  return body as T;
}

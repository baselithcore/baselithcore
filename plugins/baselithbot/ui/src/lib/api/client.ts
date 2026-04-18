export const API_BASE = '/baselithbot';
export const DASH = `${API_BASE}/dash`;
const DASHBOARD_TOKEN_STORAGE_KEY = 'baselithbot.dashboard.token';

function readDashboardTokenFromQuery(): string | null {
  if (typeof window === 'undefined') return null;
  const token = new URLSearchParams(window.location.search).get('token')?.trim();
  return token || null;
}

function getDashboardToken(): string | null {
  if (typeof window === 'undefined') return null;

  const queryToken = readDashboardTokenFromQuery();
  if (queryToken) {
    try {
      window.sessionStorage.setItem(DASHBOARD_TOKEN_STORAGE_KEY, queryToken);
    } catch {
      /* ignore sessionStorage failures */
    }
    return queryToken;
  }

  try {
    const stored = window.sessionStorage.getItem(DASHBOARD_TOKEN_STORAGE_KEY)?.trim();
    return stored || null;
  } catch {
    return null;
  }
}

export function withDashboardToken(path: string): string {
  const token = getDashboardToken();
  if (!token || typeof window === 'undefined') return path;

  const url = new URL(path, window.location.origin);
  if (!url.searchParams.has('token')) {
    url.searchParams.set('token', token);
  }
  return `${url.pathname}${url.search}${url.hash}`;
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
  const res = await fetch(withDashboardToken(path), {
    credentials: 'same-origin',
    headers: {
      'Content-Type': 'application/json',
      Accept: 'application/json',
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
      ...(init.headers ?? {}),
    },
    ...init,
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

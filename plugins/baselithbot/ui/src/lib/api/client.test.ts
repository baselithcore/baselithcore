import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { ApiError, DASHBOARD_TOKEN_STORAGE_KEY, request } from './client';

function jsonResponse(body: unknown, status = 200): Response {
  return {
    ok: status >= 200 && status < 300,
    status,
    statusText: status >= 200 && status < 300 ? 'OK' : 'Error',
    text: async () => JSON.stringify(body),
  } as Response;
}

describe('api client request()', () => {
  beforeEach(() => {
    window.sessionStorage.clear();
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it('sends the dashboard token as an Authorization header, never as a ?token= query param', async () => {
    window.sessionStorage.setItem(DASHBOARD_TOKEN_STORAGE_KEY, 'super-secret');
    const fetchMock = vi.fn(async (_input: RequestInfo | URL, _init?: RequestInit) =>
      jsonResponse({ ok: true })
    );
    vi.stubGlobal('fetch', fetchMock);

    await request('/baselithbot/dash/overview');

    expect(fetchMock).toHaveBeenCalledTimes(1);
    const call = fetchMock.mock.calls[0]!;
    const [calledUrl, init] = call;

    // The historical leak (withDashboardToken) rewrote the URL to append
    // `?token=...` on every request. It must be gone: the URL passed to
    // fetch is exactly the caller's path, untouched.
    expect(calledUrl).toBe('/baselithbot/dash/overview');
    expect(String(calledUrl)).not.toContain('token=');

    const headers = new Headers(init?.headers);
    expect(headers.get('Authorization')).toBe('Bearer super-secret');
  });

  it('omits the Authorization header, and never adds a token param, when no token is stored', async () => {
    const fetchMock = vi.fn(async (_input: RequestInfo | URL, _init?: RequestInit) =>
      jsonResponse({ ok: true })
    );
    vi.stubGlobal('fetch', fetchMock);

    await request('/baselithbot/dash/overview?existing=1');

    const [calledUrl, init] = fetchMock.mock.calls[0]!;
    expect(calledUrl).toBe('/baselithbot/dash/overview?existing=1');
    const headers = new Headers(init?.headers);
    expect(headers.get('Authorization')).toBeNull();
  });

  it('keeps the Authorization header when the caller passes its own headers', async () => {
    window.sessionStorage.setItem(DASHBOARD_TOKEN_STORAGE_KEY, 'super-secret');
    const fetchMock = vi.fn(async (_input: RequestInfo | URL, _init?: RequestInit) =>
      jsonResponse({ ok: true })
    );
    vi.stubGlobal('fetch', fetchMock);

    await request('/baselithbot/dash/overview', {
      method: 'POST',
      headers: { 'X-Extra': '1' },
      credentials: 'include',
    });

    const [, init] = fetchMock.mock.calls[0]!;
    const headers = new Headers(init?.headers);
    expect(headers.get('Authorization')).toBe('Bearer super-secret');
    expect(headers.get('X-Extra')).toBe('1');
    expect(headers.get('Content-Type')).toBe('application/json');
    expect(init?.method).toBe('POST');
    expect(init?.credentials).toBe('same-origin');
  });

  it('throws ApiError carrying the response detail on a non-2xx status', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => jsonResponse({ detail: 'invalid dashboard bearer token' }, 403))
    );

    await expect(request('/baselithbot/dash/overview')).rejects.toBeInstanceOf(ApiError);
  });
});

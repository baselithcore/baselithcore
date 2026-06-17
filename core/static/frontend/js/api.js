/* API client for the console — dependency-free, same-origin (CSP: script-src 'self').
 * Wraps the BaselithCore REST API: auth via X-API-Key, the standardized error
 * envelope, and chat streaming. */

const KEY_STORAGE = 'baselith.apiKey';

export class ApiError extends Error {
  constructor(message, { status, code, requestId } = {}) {
    super(message);
    this.name = 'ApiError';
    this.status = status;
    this.code = code;
    this.requestId = requestId;
  }
}

export function getKey() {
  return localStorage.getItem(KEY_STORAGE) || '';
}

// The console is an operator tool: the user pastes *their own* API key so the
// browser can call the API on their behalf. Persisting it in localStorage is the
// standard same-origin pattern for a single-page admin console — there is no
// more-secure browser store for a client-held bearer credential (sessionStorage
// is equivalent; httpOnly cookies require a server-side session the console does
// not have). Scope keys narrowly (see API_KEYS_SCOPED) to bound exposure.
// (CodeQL js/clear-text-storage-of-sensitive-data is accepted here.)
export function setKey(value) {
  if (value) localStorage.setItem(KEY_STORAGE, value);
  else localStorage.removeItem(KEY_STORAGE);
}

export function clearKey() {
  localStorage.removeItem(KEY_STORAGE);
}

function authHeaders(base) {
  const h = Object.assign({}, base);
  const k = getKey();
  if (k) h['X-API-Key'] = k;
  return h;
}

async function decodeError(res) {
  let code;
  let message = 'HTTP ' + res.status;
  let requestId = res.headers.get('X-Request-ID') || undefined;
  try {
    const body = await res.json();
    if (body && body.error) {
      code = body.error.code;
      message = body.error.message || message;
      requestId = body.error.request_id || requestId;
    } else if (body && body.detail) {
      message = typeof body.detail === 'string' ? body.detail : JSON.stringify(body.detail);
    }
  } catch {
    /* non-JSON body — keep the status message */
  }
  return new ApiError(message, { status: res.status, code, requestId });
}

/** Perform a JSON request. Returns parsed JSON (or null on 204); throws ApiError. */
export async function request(method, path, { body, headers } = {}) {
  const init = { method, headers: authHeaders(headers || {}) };
  if (body !== undefined) {
    init.headers['Content-Type'] = 'application/json';
    init.body = JSON.stringify(body);
  }
  let res;
  try {
    res = await fetch(path, init);
  } catch (err) {
    throw new ApiError('Network error: ' + (err && err.message ? err.message : 'failed'), {
      status: 0,
    });
  }
  if (!res.ok) throw await decodeError(res);
  if (res.status === 204) return null;
  const ctype = res.headers.get('content-type') || '';
  return ctype.includes('application/json') ? res.json() : res.text();
}

/** Stream a chat response, invoking onChunk(text) for each chunk.
 *  Returns true if streaming was used, false if unsupported (caller falls back). */
export async function streamChat(payload, onChunk) {
  const res = await fetch('/chat/stream', {
    method: 'POST',
    headers: authHeaders({ 'Content-Type': 'application/json' }),
    body: JSON.stringify(payload),
  });
  if (!res.ok) throw await decodeError(res);
  if (!res.body || !res.body.getReader) return false;
  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  for (;;) {
    const { value, done } = await reader.read();
    if (done) break;
    onChunk(decoder.decode(value, { stream: true }));
  }
  return true;
}

/** Human-friendly message for an ApiError. */
export function friendlyError(err) {
  const status = err && err.status;
  if (status === 401) return 'Unauthorized (401). Set a valid API key.';
  if (status === 403) {
    const scope = err.code === 'insufficient_scope' ? ' (missing capability scope)' : '';
    return 'Forbidden (403)' + scope + '. Your key lacks permission.';
  }
  if (status === 404) return 'Not found (404).';
  if (status === 429) return 'Rate limited (429). Slow down and retry.';
  if (status && status >= 500) return 'Server error (' + status + ').';
  if (status) return (err.message || 'Request failed') + ' (HTTP ' + status + ').';
  return err && err.message ? err.message : 'Request failed.';
}

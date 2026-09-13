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
  return sessionStorage.getItem(KEY_STORAGE) || '';
}

/** Whether a key is currently stored, without reading its value. */
export function hasKey() {
  return sessionStorage.getItem(KEY_STORAGE) !== null;
}

// The console is an operator tool: the user pastes *their own* API key so the
// browser can call the API on their behalf. Holding it in sessionStorage is the
// standard same-origin pattern for a single-page admin console — there is no
// more-secure browser store for a client-held bearer credential (localStorage is
// equivalent but outlives the tab; httpOnly cookies require a server-side session
// the console does not have). The key is never written back into the DOM once
// stored, so it leaves storage only as an X-API-Key request header. Scope keys
// narrowly (see API_KEYS_SCOPED) to bound exposure.
// (CodeQL js/clear-text-storage-of-sensitive-data is accepted here.)
export function setKey(value) {
  if (value) sessionStorage.setItem(KEY_STORAGE, value);
  else sessionStorage.removeItem(KEY_STORAGE);
}

export function clearKey() {
  sessionStorage.removeItem(KEY_STORAGE);
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

/** Parse one blank-line-delimited SSE block into `{event, data}`.
 *  Returns null for a block with no event content at all (e.g. one made only
 *  of `: keepalive`-style comment lines). Internal to streamChat. */
function parseSseBlock(block) {
  let event = null;
  const dataLines = [];
  for (const line of block.split('\n')) {
    if (!line || line.startsWith(':')) continue; // blank line, or a comment (keepalive)
    if (line.startsWith('event:')) {
      event = line.slice('event:'.length).trim();
    } else if (line.startsWith('data:')) {
      let value = line.slice('data:'.length);
      if (value.startsWith(' ')) value = value.slice(1);
      dataLines.push(value);
    }
  }
  if (event === null && dataLines.length === 0) return null;
  return { event, data: dataLines.join('\n') };
}

/** Incremental text -> SSE-event decoder. Buffers raw text across chunk
 *  boundaries (a `data:` line, or the blank line ending an event, can arrive
 *  split across two reads) and yields one `{event, data}` per complete,
 *  blank-line-terminated block. Internal to streamChat. */
class SseDecoder {
  constructor() {
    this._buffer = '';
    // A chunk boundary can fall exactly between a "\r" and its "\n":
    // normalising a trailing "\r" on the spot would make a "\n" arriving at
    // the start of the *next* feed() read as a second, spurious line break
    // instead of completing the same "\r\n". So a trailing bare "\r" is held
    // back (not normalised yet) until the next feed() or flush() resolves it,
    // one way or the other.
    this._pendingCr = false;
  }

  feed(text) {
    if (this._pendingCr) {
      text = '\r' + text;
      this._pendingCr = false;
    }
    if (text.endsWith('\r')) {
      text = text.slice(0, -1);
      this._pendingCr = true;
    }
    this._buffer += text.replace(/\r\n/g, '\n').replace(/\r/g, '\n');
    const events = [];
    let idx;
    while ((idx = this._buffer.indexOf('\n\n')) !== -1) {
      const block = this._buffer.slice(0, idx);
      this._buffer = this._buffer.slice(idx + 2);
      const event = parseSseBlock(block);
      if (event) events.push(event);
    }
    return events;
  }

  /** One final event from a trailing, unterminated buffer (if any). */
  flush() {
    if (this._pendingCr) {
      this._buffer += '\n';
      this._pendingCr = false;
    }
    if (!this._buffer.trim()) return [];
    const event = parseSseBlock(this._buffer);
    this._buffer = '';
    return event ? [event] : [];
  }
}

/** Stream a chat response, invoking onChunk(text) for each decoded SSE chunk.
 *  Decodes the wire format emitted by plugins/api_routers/chat.py: splits on
 *  blank lines, reassembles multi-line `data:` fields, ignores `: keepalive`
 *  comment lines, stops at `event: done`.
 *  Returns true if streaming was used, false if unsupported (caller falls back).
 *  Throws ApiError if the server frames `event: error` mid-stream. */
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
  const sse = new SseDecoder();
  // Returns true once `event: done` is seen, so the caller can stop reading.
  const handleEvents = (events) => {
    for (const event of events) {
      if (event.event === 'done') return true;
      if (event.event === 'error') throw new ApiError(event.data || 'stream failed');
      onChunk(event.data);
    }
    return false;
  };
  for (;;) {
    const { value, done } = await reader.read();
    if (done) break;
    if (!value) continue;
    if (handleEvents(sse.feed(decoder.decode(value, { stream: true })))) return true;
  }
  const tail = decoder.decode();
  if (tail && handleEvents(sse.feed(tail))) return true;
  handleEvents(sse.flush());
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

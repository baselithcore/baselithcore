/**
 * Transport helpers shared by `client.ts` (which keeps only the API methods
 * the `sdk-contract` gate reads): URL/query/path building, backoff, body
 * decoding, and the "was the request ever sent?" classifier behind the
 * per-call retry policy.
 */

/** Trim leading and/or trailing '/' in linear time (no regex → no ReDoS). */
export function trimSlashes(s: string, opts: { leading?: boolean; trailing?: boolean }): string {
  let start = 0;
  let end = s.length;
  if (opts.leading) while (start < end && s.charCodeAt(start) === 47) start++;
  if (opts.trailing) while (end > start && s.charCodeAt(end - 1) === 47) end--;
  return s.slice(start, end);
}

export const sleep = (ms: number): Promise<void> =>
  new Promise((resolve) => setTimeout(resolve, ms));

export function backoffMs(attempt: number, retryAfter?: number): number {
  if (retryAfter !== undefined && retryAfter >= 0) return retryAfter * 1000;
  return Math.min(2 ** attempt, 30) * 1000 + Math.random() * 500;
}

export function parseRetryAfter(value: string | null): number | undefined {
  if (!value) return undefined;
  const n = Number(value);
  return Number.isFinite(n) ? n : undefined;
}

/** Base64 of a UTF-8 string (btoa alone rejects non-Latin-1 input). */
export function base64Utf8(text: string): string {
  let binary = '';
  for (const byte of new TextEncoder().encode(text)) binary += String.fromCharCode(byte);
  return btoa(binary);
}

/** Substitute `{name}` placeholders, percent-encoding every value. */
export function fillPath(path: string, params?: Record<string, string | number>): string {
  if (!params) return path;
  let out = path;
  for (const [name, value] of Object.entries(params)) {
    out = out.split(`{${name}}`).join(encodeURIComponent(String(value)));
  }
  return out;
}

/** Query string from the defined values only (`undefined`/`null` are omitted). */
export function queryString(query?: Record<string, string | number | null | undefined>): string {
  if (!query) return '';
  const qs = new URLSearchParams();
  for (const [k, v] of Object.entries(query))
    if (v !== undefined && v !== null) qs.set(k, String(v));
  const text = qs.toString();
  return text ? `?${text}` : '';
}

export async function decodeBody(res: Response): Promise<unknown> {
  const ctype = (res.headers.get('content-type') ?? '').toLowerCase();
  // `application/json` and every `+json` structured suffix — above all the
  // RFC 9457 `application/problem+json` the server answers errors with.
  if (ctype.includes('application/json') || ctype.includes('+json')) {
    try {
      return await res.json();
    } catch {
      return await res.text();
    }
  }
  return await res.text();
}

/**
 * Error codes (Node/undici) of a failure that happened before any byte of the
 * request was sent: connection refused, DNS failure, unreachable host, or a
 * connect timeout. A reset or a socket error is *not* here — it can strike
 * after the request was written, while the server is already handling it.
 */
const NOT_SENT_CODES: ReadonlySet<string> = new Set([
  'ECONNREFUSED',
  'ENOTFOUND',
  'EAI_AGAIN',
  'EHOSTUNREACH',
  'ENETUNREACH',
  'UND_ERR_CONNECT_TIMEOUT',
]);

/** `true` when `err` (or any error in its `cause` chain) carries a not-sent code. */
export function isNotSentError(err: unknown): boolean {
  let cur: unknown = err;
  for (let depth = 0; cur && typeof cur === 'object' && depth < 5; depth++) {
    const code = (cur as { code?: unknown }).code;
    if (typeof code === 'string' && NOT_SENT_CODES.has(code)) return true;
    cur = (cur as { cause?: unknown }).cause;
  }
  return false;
}

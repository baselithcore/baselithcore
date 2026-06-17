/**
 * Typed client for the BaselithCore API.
 *
 * Built on the platform `fetch` (Node >=18, browsers, edge runtimes) with no
 * runtime dependencies. Features: API-key/bearer auth, retry with backoff +
 * jitter on 429/5xx (honouring `Retry-After`), idempotency keys, streaming, and
 * a typed error hierarchy parsed from the API's error envelope.
 *
 * @example
 * ```ts
 * const client = new BaselithClient({ baseUrl: "https://api.example.com", apiKey: "sk-..." });
 * const res = await client.chat("hello");
 * console.log(res.answer);
 * for await (const chunk of client.chatStream("tell me a story")) process.stdout.write(chunk);
 * ```
 */

import { ApiConnectionError, errorFromResponse } from './errors.js';
import type {
  ChatRequest,
  ChatResponse,
  FeedbackRequest,
  HealthStatus,
  ReadinessStatus,
} from './models.js';

type FetchImpl = (input: string, init?: RequestInit) => Promise<Response>;

const DEFAULT_TIMEOUT_MS = 30_000;
const DEFAULT_MAX_RETRIES = 2;
const RETRYABLE_STATUS = new Set([429, 500, 502, 503, 504]);
const VERSION = '0.1.0';
const USER_AGENT = `baselith-sdk-ts/${VERSION}`;

export interface BaselithClientOptions {
  baseUrl: string;
  apiKey?: string;
  bearerToken?: string;
  tenantId?: string;
  /** Path prefix for data endpoints; `null` to call unversioned paths. */
  apiVersion?: string | null;
  timeoutMs?: number;
  maxRetries?: number;
  /** Override the `fetch` implementation (testing / custom agents). */
  fetchImpl?: FetchImpl;
}

/** Trim leading and/or trailing '/' in linear time (no regex → no ReDoS). */
function trimSlashes(s: string, opts: { leading?: boolean; trailing?: boolean }): string {
  let start = 0;
  let end = s.length;
  if (opts.leading) while (start < end && s.charCodeAt(start) === 47) start++;
  if (opts.trailing) while (end > start && s.charCodeAt(end - 1) === 47) end--;
  return s.slice(start, end);
}

const sleep = (ms: number): Promise<void> => new Promise((resolve) => setTimeout(resolve, ms));

function backoffMs(attempt: number, retryAfter?: number): number {
  if (retryAfter !== undefined && retryAfter >= 0) return retryAfter * 1000;
  return Math.min(2 ** attempt, 30) * 1000 + Math.random() * 500;
}

function parseRetryAfter(value: string | null): number | undefined {
  if (!value) return undefined;
  const n = Number(value);
  return Number.isFinite(n) ? n : undefined;
}

async function decodeBody(res: Response): Promise<unknown> {
  const ctype = res.headers.get('content-type') ?? '';
  if (ctype.includes('application/json')) {
    try {
      return await res.json();
    } catch {
      return await res.text();
    }
  }
  return await res.text();
}

export class BaselithClient {
  private readonly baseUrl: string;
  private readonly apiVersion: string | null;
  private readonly timeoutMs: number;
  private readonly maxRetries: number;
  private readonly defaultHeaders: Record<string, string>;
  private readonly fetchImpl: FetchImpl;

  constructor(opts: BaselithClientOptions) {
    if (!opts.baseUrl) throw new Error('baseUrl is required');
    this.baseUrl = trimSlashes(opts.baseUrl, { trailing: true });
    this.apiVersion =
      opts.apiVersion === undefined
        ? 'v1'
        : opts.apiVersion == null
          ? null
          : trimSlashes(opts.apiVersion, { leading: true, trailing: true });
    this.timeoutMs = opts.timeoutMs ?? DEFAULT_TIMEOUT_MS;
    this.maxRetries = Math.max(0, opts.maxRetries ?? DEFAULT_MAX_RETRIES);
    this.fetchImpl = opts.fetchImpl ?? ((input, init) => fetch(input, init));

    const headers: Record<string, string> = {
      'User-Agent': USER_AGENT,
      Accept: 'application/json',
    };
    if (opts.apiKey) headers['x-api-key'] = opts.apiKey;
    if (opts.bearerToken) headers['Authorization'] = `Bearer ${opts.bearerToken}`;
    if (opts.tenantId) headers['X-Tenant-ID'] = opts.tenantId;
    this.defaultHeaders = headers;
  }

  private url(path: string, versioned = true): string {
    const p = '/' + trimSlashes(path, { leading: true });
    if (versioned && this.apiVersion) return `${this.baseUrl}/${this.apiVersion}${p}`;
    return `${this.baseUrl}${p}`;
  }

  private headers(extra?: Record<string, string>): Record<string, string> {
    return { ...this.defaultHeaders, ...extra };
  }

  private async rawFetch(url: string, init: RequestInit): Promise<Response> {
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), this.timeoutMs);
    try {
      return await this.fetchImpl(url, { ...init, signal: controller.signal });
    } catch (e) {
      throw new ApiConnectionError(e instanceof Error ? e.message : 'request failed');
    } finally {
      clearTimeout(timer);
    }
  }

  private async request(
    method: string,
    path: string,
    opts: {
      versioned?: boolean;
      body?: unknown;
      idempotencyKey?: string;
    } = {}
  ): Promise<Response> {
    const url = this.url(path, opts.versioned ?? true);
    const extra: Record<string, string> = {};
    if (opts.body !== undefined) extra['Content-Type'] = 'application/json';
    if (opts.idempotencyKey) extra['Idempotency-Key'] = opts.idempotencyKey;
    const init: RequestInit = {
      method,
      headers: this.headers(extra),
      body: opts.body !== undefined ? JSON.stringify(opts.body) : undefined,
    };

    let lastErr: unknown;
    for (let attempt = 0; attempt <= this.maxRetries; attempt++) {
      let res: Response;
      try {
        res = await this.rawFetch(url, init);
      } catch (e) {
        lastErr = e;
        if (attempt >= this.maxRetries) throw e;
        await sleep(backoffMs(attempt));
        continue;
      }
      if (RETRYABLE_STATUS.has(res.status) && attempt < this.maxRetries) {
        await sleep(backoffMs(attempt, parseRetryAfter(res.headers.get('Retry-After'))));
        continue;
      }
      if (res.status >= 400) {
        throw errorFromResponse(
          res.status,
          await decodeBody(res),
          res.headers.get('X-Request-ID') ?? undefined,
          parseRetryAfter(res.headers.get('Retry-After'))
        );
      }
      return res;
    }
    throw lastErr instanceof Error ? lastErr : new ApiConnectionError('request failed');
  }

  /** Send a query to the agent and return the typed response. */
  async chat(query: string, opts: Partial<ChatRequest> = {}): Promise<ChatResponse> {
    const body: ChatRequest = { query, ...opts };
    const res = await this.request('POST', '/chat', { body });
    return (await res.json()) as ChatResponse;
  }

  /** Stream the agent's answer as text chunks. */
  async *chatStream(query: string, opts: Partial<ChatRequest> = {}): AsyncGenerator<string> {
    const body: ChatRequest = { query, ...opts };
    const res = await this.rawFetch(this.url('/chat/stream'), {
      method: 'POST',
      headers: this.headers({ 'Content-Type': 'application/json' }),
      body: JSON.stringify(body),
    });
    if (res.status >= 400) {
      throw errorFromResponse(
        res.status,
        await decodeBody(res),
        res.headers.get('X-Request-ID') ?? undefined
      );
    }
    if (!res.body) {
      const text = await res.text();
      if (text) yield text;
      return;
    }
    const reader = res.body.getReader();
    const decoder = new TextDecoder();
    for (;;) {
      const { done, value } = await reader.read();
      if (done) break;
      if (value) yield decoder.decode(value, { stream: true });
    }
    const tail = decoder.decode();
    if (tail) yield tail;
  }

  /** Record feedback on a generated answer (idempotency key auto-generated). */
  async submitFeedback(
    feedback: FeedbackRequest,
    idempotencyKey?: string
  ): Promise<Record<string, unknown>> {
    const res = await this.request('POST', '/feedback', {
      body: feedback,
      idempotencyKey: idempotencyKey ?? crypto.randomUUID(),
    });
    return (await res.json()) as Record<string, unknown>;
  }

  /** Liveness probe (unauthenticated, unversioned). */
  async health(): Promise<HealthStatus> {
    const res = await this.request('GET', '/health', { versioned: false });
    return (await res.json()) as HealthStatus;
  }

  /** Readiness probe (unauthenticated, unversioned). */
  async readiness(): Promise<ReadinessStatus> {
    const res = await this.request('GET', '/health/ready', { versioned: false });
    return (await res.json()) as ReadinessStatus;
  }
}

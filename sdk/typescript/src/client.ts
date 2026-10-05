/**
 * Typed client for the BaselithCore API.
 *
 * Built on the platform `fetch` (Node >=18, browsers, edge runtimes) with no
 * runtime dependencies. Features: API-key/bearer/Basic auth, retry with
 * backoff + jitter on 429/5xx (honouring `Retry-After`; a non-idempotent call
 * — approval decision, run resume, delivery replay — is re-sent only when it
 * provably never left the client or got a 429), idempotency keys,
 * streaming, cursor pagination, and a typed error hierarchy parsed from the
 * API's error envelope. Covers chat, feedback, probes, async agent runs, run
 * event streams and history, approvals, and webhooks.
 *
 * Routes are written as their OpenAPI templates (`/agent/status/{task_id}`)
 * with `pathParams` filled in by `request()`, so the `sdk-contract` gate can
 * read them from this file.
 *
 * @example
 * ```ts
 * const client = new BaselithClient({ baseUrl: "https://api.example.com", apiKey: "sk-..." });
 * const res = await client.chat("hello");
 * console.log(res.answer);
 * for await (const chunk of client.chatStream("tell me a story")) process.stdout.write(chunk);
 * ```
 */

import { ApiConnectionError, BaselithConfigError, errorFromResponse } from './errors.js';
import type {
  AgentRunRequest,
  AgentRunStatus,
  AgentRunSubmission,
  ApprovalDecisionRequest,
  ApprovalDecisionResult,
  ApprovalPage,
  ChatRequest,
  ChatResponse,
  FeedbackRequest,
  HealthStatus,
  Page,
  ReadinessStatus,
  RunEvent,
  RunHistoryPage,
  RunResumeResult,
  WebhookCreated,
  WebhookCreateRequest,
  WebhookDeleted,
  WebhookDeliveryPage,
  WebhookPage,
  WebhookReplay,
} from './models.js';
import {
  backoffMs,
  base64Utf8,
  decodeBody,
  fillPath,
  isNotSentError,
  parseRetryAfter,
  queryString,
  sleep,
  trimSlashes,
} from './http.js';
import { iterPages, pollUntilTerminal, type PageOptions } from './pagination.js';
import { decodeRunEvents, decodeSseStream, readTextChunks } from './sse.js';

export { ChatStreamError } from './sse.js';

type FetchImpl = (input: string, init?: RequestInit) => Promise<Response>;

const DEFAULT_TIMEOUT_MS = 30_000;
const DEFAULT_MAX_RETRIES = 2;
/** Default `resumeRun` timeout: the server runs the resumed loop in-request (~600 s). */
const DEFAULT_RESUME_TIMEOUT_MS = 660_000;
const RETRYABLE_STATUS = new Set([429, 500, 502, 503, 504]);
/** Statuses a non-idempotent call may retry: refused before any work was done. */
const UNSAFE_RETRYABLE_STATUS = new Set([429]);
const VERSION = '0.42.1';
const USER_AGENT = `baselith-sdk-ts/${VERSION}`;

export interface BaselithClientOptions {
  baseUrl: string;
  apiKey?: string;
  bearerToken?: string;
  /** HTTP Basic credentials — the approvals and runs routes take the admin ones. */
  basicAuth?: { username: string; password: string };
  tenantId?: string;
  /** Path prefix for data endpoints; `null` to call unversioned paths. */
  apiVersion?: string | null;
  timeoutMs?: number;
  maxRetries?: number;
  /** Override the `fetch` implementation (testing / custom agents). */
  fetchImpl?: FetchImpl;
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
    if (opts.bearerToken && opts.basicAuth) {
      throw new BaselithConfigError('bearerToken and basicAuth both set Authorization; pass one');
    }
    if (opts.bearerToken) headers['Authorization'] = `Bearer ${opts.bearerToken}`;
    if (opts.basicAuth) {
      const { username, password } = opts.basicAuth;
      headers['Authorization'] = `Basic ${base64Utf8(`${username}:${password}`)}`;
    }
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

  /**
   * One `fetch`, aborted if the response headers take longer than `timeoutMs`.
   * The body is read after the timer is cleared, so a long SSE stream is never
   * cut by it; `rawFetch` failures carry `notSent` for the retry policy.
   */
  private async rawFetch(
    url: string,
    init: RequestInit,
    timeoutMs = this.timeoutMs
  ): Promise<Response> {
    const controller = new AbortController();
    let timedOut = false;
    const timer = setTimeout(() => {
      timedOut = true;
      controller.abort();
    }, timeoutMs);
    try {
      return await this.fetchImpl(url, { ...init, signal: controller.signal });
    } catch (e) {
      const message = timedOut
        ? `request timed out after ${timeoutMs} ms`
        : e instanceof Error
          ? e.message
          : 'request failed';
      throw new ApiConnectionError(message, { notSent: !timedOut && isNotSentError(e) });
    } finally {
      clearTimeout(timer);
    }
  }

  private async request(
    method: string,
    path: string,
    opts: {
      versioned?: boolean;
      pathParams?: Record<string, string | number>;
      query?: Record<string, string | number | null | undefined>;
      body?: unknown;
      idempotencyKey?: string;
      headers?: Record<string, string>;
      /** Non-idempotent: no re-send after a timeout, a mid-request failure or a 5xx. */
      unsafe?: boolean;
      timeoutMs?: number;
    } = {}
  ): Promise<Response> {
    const url =
      this.url(fillPath(path, opts.pathParams), opts.versioned ?? true) + queryString(opts.query);
    const extra: Record<string, string> = {};
    if (opts.headers) Object.assign(extra, opts.headers);
    if (opts.body !== undefined) extra['Content-Type'] = 'application/json';
    if (opts.idempotencyKey) extra['Idempotency-Key'] = opts.idempotencyKey;
    const init: RequestInit = {
      method,
      headers: this.headers(extra),
      body: opts.body !== undefined ? JSON.stringify(opts.body) : undefined,
    };

    const retryStatus = opts.unsafe ? UNSAFE_RETRYABLE_STATUS : RETRYABLE_STATUS;
    let lastErr: unknown;
    for (let attempt = 0; attempt <= this.maxRetries; attempt++) {
      let res: Response;
      try {
        res = await this.rawFetch(url, init, opts.timeoutMs);
      } catch (e) {
        lastErr = e;
        const retryable = !opts.unsafe || (e instanceof ApiConnectionError && e.notSent);
        if (attempt >= this.maxRetries || !retryable) throw e;
        await sleep(backoffMs(attempt));
        continue;
      }
      if (retryStatus.has(res.status) && attempt < this.maxRetries) {
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

  /**
   * Stream the agent's answer as text chunks.
   *
   * The wire format is Server-Sent Events (see {@link ChatStreamError} for
   * the mid-stream failure case); this decodes the frames and yields just
   * the text.
   */
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
      yield* decodeSseStream(text ? [text] : []);
      return;
    }
    yield* decodeSseStream(readTextChunks(res.body.getReader()));
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

  // --- Async agent runs ---

  /** Queue an agent run; returns its `task_id` and poll URL (`202 Accepted`). */
  async submitAgentRun(
    query: string,
    opts: Omit<AgentRunRequest, 'query'> & { idempotencyKey?: string } = {}
  ): Promise<AgentRunSubmission> {
    const { idempotencyKey, ...rest } = opts;
    const body: AgentRunRequest = { query, ...rest };
    const res = await this.request('POST', '/agent/async', {
      body,
      idempotencyKey: idempotencyKey ?? crypto.randomUUID(),
    });
    const sub = (await res.json()) as AgentRunSubmission;
    const location = res.headers.get('Location');
    return location ? { ...sub, location } : sub;
  }

  /** Current status of a queued run (404 for unknown or other-tenant ids). */
  async getAgentRun(taskId: string): Promise<AgentRunStatus> {
    const res = await this.request('GET', '/agent/status/{task_id}', {
      pathParams: { task_id: taskId },
    });
    return (await res.json()) as AgentRunStatus;
  }

  /**
   * Poll until the run is completed, failed or cancelled.
   * Throws {@link RunTimeoutError} once `timeoutMs` (default 300000) elapses.
   */
  async waitForRun(
    taskId: string,
    opts: { timeoutMs?: number; pollIntervalMs?: number } = {}
  ): Promise<AgentRunStatus> {
    return pollUntilTerminal(
      (id) => this.getAgentRun(id),
      taskId,
      opts.timeoutMs ?? 300_000,
      opts.pollIntervalMs ?? 2_000
    );
  }

  // --- Runs: events and history ---

  /**
   * Stream a run's structured events (SSE) until its terminal event.
   *
   * Subscribe before starting or resuming the run: the feed is fan-out only,
   * not replayed (`lastEventId` is sent but cannot rewind it).
   */
  async *streamRunEvents(
    runId: string,
    opts: { lastEventId?: string } = {}
  ): AsyncGenerator<RunEvent> {
    const res = await this.request('GET', '/runs/{run_id}/events', {
      pathParams: { run_id: runId },
      headers: {
        Accept: 'text/event-stream',
        ...(opts.lastEventId ? { 'Last-Event-ID': opts.lastEventId } : {}),
      },
    });
    if (!res.body) {
      const text = await res.text();
      yield* decodeRunEvents(text ? [text] : []);
      return;
    }
    yield* decodeRunEvents(readTextChunks(res.body.getReader()));
  }

  /** One page of a run's version-ascending snapshot summaries. */
  async getRunHistory(runId: string, opts: PageOptions = {}): Promise<RunHistoryPage> {
    const res = await this.request('GET', '/runs/{run_id}/history', {
      pathParams: { run_id: runId },
      query: { limit: opts.limit, cursor: opts.cursor },
    });
    return (await res.json()) as RunHistoryPage;
  }

  // --- Approvals ---

  /** One page of runs paused awaiting a decision (newest first). */
  async listApprovals(opts: PageOptions & { tenantId?: string } = {}): Promise<ApprovalPage> {
    const res = await this.request('GET', '/approvals', {
      query: { tenant_id: opts.tenantId, limit: opts.limit, cursor: opts.cursor },
    });
    return (await res.json()) as ApprovalPage;
  }

  /**
   * Record an approve/deny decision; `approver` is a display label only.
   * Sent with an `Idempotency-Key` (auto-generated unless given) and never
   * re-sent after a timeout or 5xx — retry with the same key instead.
   */
  async decideApproval(
    runId: string,
    decision: ApprovalDecisionRequest,
    opts: { idempotencyKey?: string } = {}
  ): Promise<ApprovalDecisionResult> {
    const res = await this.request('POST', '/approvals/{run_id}/decision', {
      pathParams: { run_id: runId },
      body: decision,
      idempotencyKey: opts.idempotencyKey ?? crypto.randomUUID(),
      unsafe: true,
    });
    return (await res.json()) as ApprovalDecisionResult;
  }

  /**
   * Resume a checkpointed run so the approval gate consumes the decision.
   *
   * The server runs the resumed agent loop inside this request, so the call
   * waits up to `timeoutMs` (default 660000). It carries an `Idempotency-Key`
   * and is never re-sent after a timeout or 5xx — the loop may still be
   * running; retry with the same `idempotencyKey`.
   */
  async resumeRun(
    runId: string,
    opts: { timeoutMs?: number; idempotencyKey?: string } = {}
  ): Promise<RunResumeResult> {
    const res = await this.request('POST', '/approvals/{run_id}/resume', {
      pathParams: { run_id: runId },
      idempotencyKey: opts.idempotencyKey ?? crypto.randomUUID(),
      unsafe: true,
      timeoutMs: opts.timeoutMs ?? DEFAULT_RESUME_TIMEOUT_MS,
    });
    return (await res.json()) as RunResumeResult;
  }

  // --- Webhooks ---

  /** Register an endpoint (`webhooks:write`); the signing secret is returned once. */
  async createWebhook(
    webhook: WebhookCreateRequest,
    idempotencyKey?: string
  ): Promise<WebhookCreated> {
    const res = await this.request('POST', '/webhooks', {
      body: { event_types: ['*'], ...webhook },
      idempotencyKey: idempotencyKey ?? crypto.randomUUID(),
    });
    return (await res.json()) as WebhookCreated;
  }

  /** One page of the tenant's endpoints (`webhooks:read`). */
  async listWebhooks(opts: PageOptions = {}): Promise<WebhookPage> {
    const res = await this.request('GET', '/webhooks', {
      query: { limit: opts.limit, cursor: opts.cursor },
    });
    return (await res.json()) as WebhookPage;
  }

  /** Delete an endpoint (`webhooks:write`). */
  async deleteWebhook(endpointId: string): Promise<WebhookDeleted> {
    const res = await this.request('DELETE', '/webhooks/{endpoint_id}', {
      pathParams: { endpoint_id: endpointId },
    });
    return (await res.json()) as WebhookDeleted;
  }

  /** One page of the tenant's delivery records (`webhooks:read`). */
  async listWebhookDeliveries(opts: PageOptions = {}): Promise<WebhookDeliveryPage> {
    const res = await this.request('GET', '/webhooks/deliveries', {
      query: { limit: opts.limit, cursor: opts.cursor },
    });
    return (await res.json()) as WebhookDeliveryPage;
  }

  /** Re-attempt a delivery (`webhooks:write`); keyed, never re-sent on timeout/5xx. */
  async replayWebhookDelivery(
    deliveryId: string,
    opts: { idempotencyKey?: string } = {}
  ): Promise<WebhookReplay> {
    const res = await this.request('POST', '/webhooks/deliveries/{delivery_id}/replay', {
      pathParams: { delivery_id: deliveryId },
      idempotencyKey: opts.idempotencyKey ?? crypto.randomUUID(),
      unsafe: true,
    });
    return (await res.json()) as WebhookReplay;
  }

  // --- Pagination ---

  /**
   * Yield every page of a list method, following `next_cursor`.
   *
   * @example
   * ```ts
   * for await (const page of client.iterPages((o) => client.listWebhooks(o), { limit: 50 }))
   *   for (const ep of page.endpoints) console.log(ep.id);
   * ```
   */
  iterPages<O extends PageOptions, P extends Page>(
    fetch: (opts: O) => Promise<P>,
    opts?: O
  ): AsyncGenerator<P> {
    return iterPages(fetch, opts);
  }
}

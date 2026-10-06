/**
 * Typed error hierarchy for the BaselithCore SDK.
 *
 * API failures map onto these classes, parsing the server's standardized error
 * envelope: `{ "error": { code, message, type, request_id } }`.
 */

export class BaselithError extends Error {
  constructor(message: string) {
    super(message);
    this.name = new.target.name;
  }
}

/** Client constructed with an invalid configuration. */
export class BaselithConfigError extends BaselithError {}

/**
 * The request failed at the transport level (network/timeout/abort).
 *
 * `notSent` is `true` only when the failure provably happened before the
 * request left the client (connection refused, DNS failure, connect timeout):
 * a non-idempotent call (approval decision, run resume, delivery replay) is
 * retried only in that case — a timeout may mean the server is still working.
 */
export class ApiConnectionError extends BaselithError {
  readonly notSent: boolean;

  constructor(message: string, opts: { notSent?: boolean } = {}) {
    super(message);
    this.notSent = opts.notSent ?? false;
  }
}

/**
 * `waitForRun` gave up before the run reached a terminal state.
 * `lastStatus` is the most recent status record observed.
 */
export class RunTimeoutError extends BaselithError {
  readonly taskId: string;
  readonly timeoutMs: number;
  readonly lastStatus?: unknown;

  constructor(taskId: string, timeoutMs: number, lastStatus?: unknown) {
    super(`run ${taskId} not finished after ${timeoutMs}ms`);
    this.taskId = taskId;
    this.timeoutMs = timeoutMs;
    this.lastStatus = lastStatus;
  }
}

/** An error response was returned by the API. */
export class BaselithApiError extends BaselithError {
  readonly statusCode: number;
  readonly code?: string;
  readonly errorType?: string;
  readonly requestId?: string;
  readonly body?: unknown;

  constructor(
    message: string,
    opts: {
      statusCode: number;
      code?: string;
      errorType?: string;
      requestId?: string;
      body?: unknown;
    }
  ) {
    super(message);
    this.statusCode = opts.statusCode;
    this.code = opts.code;
    this.errorType = opts.errorType;
    this.requestId = opts.requestId;
    this.body = opts.body;
  }
}

/** 401 — missing or invalid credentials. */
export class AuthenticationError extends BaselithApiError {}

/** 403 — authenticated but lacking the required role/scope. */
export class PermissionDeniedError extends BaselithApiError {}

/** 404 — resource does not exist. */
export class NotFoundError extends BaselithApiError {}

/** 429 — rate limit exceeded. `retryAfter` is the server hint in seconds. */
export class RateLimitError extends BaselithApiError {
  readonly retryAfter?: number;

  constructor(
    message: string,
    opts: {
      statusCode: number;
      code?: string;
      errorType?: string;
      requestId?: string;
      body?: unknown;
      retryAfter?: number;
    }
  ) {
    super(message, opts);
    this.retryAfter = opts.retryAfter;
  }
}

/** 5xx — the server failed to handle the request. */
export class ServerError extends BaselithApiError {}

interface Envelope {
  /** Legacy envelope (pre-0.29 servers). */
  error?: { code?: string; message?: string; type?: string; request_id?: string };
  /** RFC 9457 problem+json members (current servers). */
  type?: string;
  title?: string;
  status?: number;
  detail?: unknown;
  instance?: string;
  code?: string;
  request_id?: string;
}

/** Build the most specific {@link BaselithApiError} for a response. */
export function errorFromResponse(
  statusCode: number,
  body: unknown,
  requestId?: string,
  retryAfter?: number
): BaselithApiError {
  let code: string | undefined;
  let errorType: string | undefined;
  let message = `request failed with status ${statusCode}`;

  if (body && typeof body === 'object') {
    const env = body as Envelope;
    if (env.error && typeof env.error === 'object') {
      code = env.error.code;
      errorType = env.error.type;
      message = env.error.message ?? message;
      requestId = env.error.request_id ?? requestId;
    } else if (env.code !== undefined || env.title !== undefined || env.type !== undefined) {
      // RFC 9457 application/problem+json (current server format)
      code = env.code;
      errorType = env.type;
      message =
        (typeof env.detail === 'string' && env.detail ? env.detail : undefined) ??
        env.title ??
        message;
      requestId = env.request_id ?? requestId;
    } else if (env.detail !== undefined) {
      message = typeof env.detail === 'string' ? env.detail : JSON.stringify(env.detail);
    }
  } else if (typeof body === 'string' && body) {
    message = body;
  }

  const opts = { statusCode, code, errorType, requestId, body };
  switch (true) {
    case statusCode === 401:
      return new AuthenticationError(message, opts);
    case statusCode === 403:
      return new PermissionDeniedError(message, opts);
    case statusCode === 404:
      return new NotFoundError(message, opts);
    case statusCode === 429:
      return new RateLimitError(message, { ...opts, retryAfter });
    case statusCode >= 500:
      return new ServerError(message, opts);
    default:
      return new BaselithApiError(message, opts);
  }
}

import { describe, it, expect } from 'vitest';
import {
  BaselithClient,
  AuthenticationError,
  PermissionDeniedError,
  NotFoundError,
  ServerError,
  RateLimitError,
  ApiConnectionError,
} from '../src/index.js';

const BASE = 'https://api.test';

type Handler = (url: string, init?: RequestInit) => Response | Promise<Response>;

function json(payload: unknown, status = 200, headers: Record<string, string> = {}): Response {
  return new Response(JSON.stringify(payload), {
    status,
    headers: { 'content-type': 'application/json', ...headers },
  });
}

function clientWith(handler: Handler, opts: Record<string, unknown> = {}): BaselithClient {
  return new BaselithClient({
    baseUrl: BASE,
    apiKey: 'k',
    fetchImpl: (url, init) => Promise.resolve(handler(url, init)),
    ...opts,
  });
}

describe('construction', () => {
  it('requires baseUrl', () => {
    expect(() => new BaselithClient({ baseUrl: '' })).toThrow();
  });
});

describe('routing', () => {
  it('uses the /v1 prefix for data endpoints', async () => {
    let seen = '';
    const c = clientWith((url) => {
      seen = url;
      return json({ answer: 'hi' });
    });
    await c.chat('hello');
    expect(seen).toBe(`${BASE}/v1/chat`);
  });

  it('calls health unversioned', async () => {
    let seen = '';
    const c = clientWith((url) => {
      seen = url;
      return json({ status: 'ok' });
    });
    await c.health();
    expect(seen).toBe(`${BASE}/health`);
  });

  it('respects apiVersion=null', async () => {
    let seen = '';
    const c = clientWith(
      (url) => {
        seen = url;
        return json({ answer: 'x' });
      },
      { apiVersion: null }
    );
    await c.chat('q');
    expect(seen).toBe(`${BASE}/chat`);
  });
});

describe('auth headers', () => {
  it('sends x-api-key', async () => {
    let headers: Record<string, string> = {};
    const c = clientWith((_url, init) => {
      headers = init?.headers as Record<string, string>;
      return json({ answer: 'x' });
    });
    await c.chat('q');
    expect(headers['x-api-key']).toBe('k');
    expect(headers['Authorization']).toBeUndefined();
  });

  it('sends bearer token', async () => {
    let headers: Record<string, string> = {};
    const c = new BaselithClient({
      baseUrl: BASE,
      bearerToken: 'jwt-abc',
      fetchImpl: (_url, init) => {
        headers = init?.headers as Record<string, string>;
        return Promise.resolve(json({ answer: 'x' }));
      },
    });
    await c.chat('q');
    expect(headers['Authorization']).toBe('Bearer jwt-abc');
  });

  it('sends tenant header', async () => {
    let headers: Record<string, string> = {};
    const c = clientWith(
      (_url, init) => {
        headers = init?.headers as Record<string, string>;
        return json({ answer: 'x' });
      },
      { tenantId: 'acme' }
    );
    await c.chat('q');
    expect(headers['X-Tenant-ID']).toBe('acme');
  });
});

describe('chat', () => {
  it('returns the typed response', async () => {
    const c = clientWith((_url, init) => {
      const body = JSON.parse(init?.body as string);
      expect(body.query).toBe('hello');
      return json({ answer: 'world', conversation_id: 'c1', sources: [{ id: 1 }] });
    });
    const res = await c.chat('hello');
    expect(res.answer).toBe('world');
    expect(res.conversation_id).toBe('c1');
    expect(res.sources).toEqual([{ id: 1 }]);
  });

  it('streams text chunks', async () => {
    const c = clientWith(() => new Response('Hello world'));
    let out = '';
    for await (const chunk of c.chatStream('q')) out += chunk;
    expect(out).toBe('Hello world');
  });
});

describe('feedback', () => {
  it('auto-generates an idempotency key', async () => {
    let key: string | undefined;
    const c = clientWith((_url, init) => {
      key = (init?.headers as Record<string, string>)['Idempotency-Key'];
      return json({ status: 'ok' });
    });
    const out = await c.submitFeedback({ query: 'q', answer: 'a', feedback: 'positive' });
    expect(out.status).toBe('ok');
    expect(key).toBeTruthy();
  });

  it('respects an explicit idempotency key', async () => {
    let key: string | undefined;
    const c = clientWith((_url, init) => {
      key = (init?.headers as Record<string, string>)['Idempotency-Key'];
      return json({ status: 'ok' });
    });
    await c.submitFeedback({ query: 'q', answer: 'a', feedback: 'negative' }, 'fixed-key');
    expect(key).toBe('fixed-key');
  });
});

describe('error mapping', () => {
  const cases: Array<[number, unknown]> = [
    [401, AuthenticationError],
    [403, PermissionDeniedError],
    [404, NotFoundError],
    [500, ServerError],
  ];
  for (const [status, ctor] of cases) {
    it(`maps ${status}`, async () => {
      const c = clientWith(
        () =>
          json({ error: { code: 'x', message: 'boom', type: 'T', request_id: 'r1' } }, status, {
            'X-Request-ID': 'r1',
          }),
        { maxRetries: 0 }
      );
      await expect(c.chat('q')).rejects.toBeInstanceOf(ctor as never);
    });
  }

  it('parses the envelope code and request id', async () => {
    const c = clientWith(
      () => json({ error: { code: 'insufficient_scope', message: 'no', type: 'E' } }, 403),
      { maxRetries: 0 }
    );
    try {
      await c.chat('q');
      throw new Error('should have thrown');
    } catch (e) {
      expect(e).toBeInstanceOf(PermissionDeniedError);
      expect((e as PermissionDeniedError).code).toBe('insufficient_scope');
    }
  });
});

describe('retries', () => {
  it('retries on 429 then succeeds', async () => {
    let n = 0;
    const c = clientWith(
      () => {
        n += 1;
        if (n === 1) return json({ error: { message: 'slow' } }, 429, { 'Retry-After': '0' });
        return json({ answer: 'ok' });
      },
      { maxRetries: 2 }
    );
    const res = await c.chat('q');
    expect(res.answer).toBe('ok');
    expect(n).toBe(2);
  });

  it('gives up after maxRetries', async () => {
    const c = clientWith(() => json({ error: { message: 'down' } }, 503, { 'Retry-After': '0' }), {
      maxRetries: 1,
    });
    await expect(c.chat('q')).rejects.toBeInstanceOf(ServerError);
  });

  it('exposes retryAfter on RateLimitError', async () => {
    const c = clientWith(() => json({ error: { message: 'slow' } }, 429, { 'Retry-After': '5' }), {
      maxRetries: 0,
    });
    try {
      await c.chat('q');
      throw new Error('should have thrown');
    } catch (e) {
      expect(e).toBeInstanceOf(RateLimitError);
      expect((e as RateLimitError).retryAfter).toBe(5);
    }
  });

  it('wraps connection errors', async () => {
    const c = new BaselithClient({
      baseUrl: BASE,
      apiKey: 'k',
      maxRetries: 0,
      fetchImpl: () => Promise.reject(new Error('no route')),
    });
    await expect(c.chat('q')).rejects.toBeInstanceOf(ApiConnectionError);
  });
});

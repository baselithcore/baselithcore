import { afterEach, describe, expect, it, vi } from 'vitest';
import { ApiConnectionError, BaselithClient, ServerError } from '../src/index.js';
import { readTextChunks } from '../src/sse.js';

const BASE = 'https://api.test';
const RESUME = { run_id: 'r1', result: 'done' };
const DECISION = { run_id: 'r1', recorded: true, approved: true };
const REPLAY = { status: 'success', delivery: { id: 'd1' } };

type Step = Response | Error | 'hang';

interface Seen {
  headers: Record<string, string>;
}

function json(payload: unknown, status = 200): Response {
  return new Response(JSON.stringify(payload), {
    status,
    headers: { 'content-type': 'application/json' },
  });
}

/** A client whose fetch plays `steps` in order (the last repeats). `'hang'` waits for abort. */
function scripted(steps: Array<() => Step>, opts: Record<string, unknown> = {}) {
  const seen: Seen[] = [];
  const queue = [...steps];
  const client = new BaselithClient({
    baseUrl: BASE,
    apiKey: 'k',
    maxRetries: 2,
    fetchImpl: (_url, init) => {
      seen.push({ headers: (init?.headers ?? {}) as Record<string, string> });
      const step = (queue.length > 1 ? queue.shift()! : queue[0]!)();
      if (step === 'hang') {
        return new Promise<Response>((_, reject) =>
          init?.signal?.addEventListener('abort', () => reject(new Error('aborted')))
        );
      }
      return step instanceof Error ? Promise.reject(step) : Promise.resolve(step);
    },
    ...opts,
  });
  return { client, seen };
}

/** What undici's fetch throws when the TCP connect is refused. */
function refused(): Error {
  return Object.assign(new TypeError('fetch failed'), { cause: { code: 'ECONNREFUSED' } });
}

const UNSAFE: Array<[string, (c: BaselithClient) => Promise<unknown>, unknown]> = [
  ['resumeRun', (c) => c.resumeRun('r1'), RESUME],
  ['decideApproval', (c) => c.decideApproval('r1', { approved: true }), DECISION],
  ['replayWebhookDelivery', (c) => c.replayWebhookDelivery('d1'), REPLAY],
];

afterEach(() => {
  vi.useRealTimers();
});

describe('non-idempotent calls', () => {
  it.each(UNSAFE)('%s sends an auto Idempotency-Key', async (_name, call, payload) => {
    const { client, seen } = scripted([() => json(payload)]);
    await call(client);
    expect(seen[0]!.headers['Idempotency-Key']).toMatch(/[0-9a-f-]{36}/);
  });

  it('honours explicit idempotency keys', async () => {
    const { client, seen } = scripted([
      () => json(RESUME),
      () => json(DECISION),
      () => json(REPLAY),
    ]);
    await client.resumeRun('r1', { idempotencyKey: 'k-resume' });
    await client.decideApproval('r1', { approved: true }, { idempotencyKey: 'k-decide' });
    await client.replayWebhookDelivery('d1', { idempotencyKey: 'k-replay' });
    expect(seen.map((s) => s.headers['Idempotency-Key'])).toEqual([
      'k-resume',
      'k-decide',
      'k-replay',
    ]);
  });

  it.each(UNSAFE)('%s is not re-sent after a 5xx', async (_name, call, payload) => {
    const { client, seen } = scripted([() => json({}, 503), () => json(payload)]);
    await expect(call(client)).rejects.toBeInstanceOf(ServerError);
    expect(seen).toHaveLength(1);
  });

  it('resumeRun is not re-sent after a timeout', async () => {
    const { client, seen } = scripted([() => 'hang', () => json(RESUME)]);
    const err = await client.resumeRun('r1', { timeoutMs: 20 }).catch((e: unknown) => e);
    expect(err).toBeInstanceOf(ApiConnectionError);
    expect((err as ApiConnectionError).notSent).toBe(false);
    expect((err as Error).message).toContain('timed out');
    expect(seen).toHaveLength(1);
  });

  it('is not re-sent after an unclassified network failure', async () => {
    const { client, seen } = scripted([() => new TypeError('fetch failed'), () => json(DECISION)]);
    await expect(client.decideApproval('r1', { approved: true })).rejects.toBeInstanceOf(
      ApiConnectionError
    );
    expect(seen).toHaveLength(1);
  });

  it('is retried, with the same key, when the request was never sent', async () => {
    vi.useFakeTimers();
    const { client, seen } = scripted([refused, () => json(RESUME)]);
    const pending = client.resumeRun('r1');
    await vi.runAllTimersAsync();
    expect(await pending).toEqual(RESUME);
    expect(seen).toHaveLength(2);
    expect(seen[0]!.headers['Idempotency-Key']).toBe(seen[1]!.headers['Idempotency-Key']);
  });

  it('is retried on 429', async () => {
    const rateLimited = () =>
      new Response('{}', {
        status: 429,
        headers: { 'content-type': 'application/json', 'Retry-After': '0' },
      });
    const { client, seen } = scripted([rateLimited, () => json(REPLAY)]);
    await client.replayWebhookDelivery('d1');
    expect(seen).toHaveLength(2);
  });

  it('resumeRun waits 660 s by default, overridable per call', async () => {
    vi.useFakeTimers();
    const { client } = scripted([() => 'hang']);
    let settled = false;
    const pending = client.resumeRun('r1').catch((e: unknown) => {
      settled = true;
      return e;
    });
    await vi.advanceTimersByTimeAsync(659_000);
    expect(settled).toBe(false); // a plain request would have aborted at 30 s
    await vi.advanceTimersByTimeAsync(2_000);
    expect(await pending).toBeInstanceOf(ApiConnectionError);
  });

  it('safe calls still retry on timeouts and 5xx', async () => {
    vi.useFakeTimers();
    const { client, seen } = scripted(
      [() => 'hang', () => json({}, 503), () => json({ status: 'ok' })],
      { timeoutMs: 10 }
    );
    const pending = client.health();
    await vi.runAllTimersAsync();
    expect(await pending).toEqual({ status: 'ok' });
    expect(seen).toHaveLength(3);
  });
});

describe('stream body release', () => {
  function trackedBody(chunks: string[]) {
    const state = { cancelled: false };
    const encoder = new TextEncoder();
    let i = 0;
    const body = new ReadableStream<Uint8Array>({
      pull(controller) {
        if (i < chunks.length) controller.enqueue(encoder.encode(chunks[i++]!));
        // never closes on its own: only a cancel ends it, like a live SSE feed
      },
      cancel() {
        state.cancelled = true;
      },
    });
    return { body, state };
  }

  it('readTextChunks cancels the reader when the consumer breaks early', async () => {
    const { body, state } = trackedBody(['a', 'b', 'c']);
    const reader = body.getReader();
    for await (const chunk of readTextChunks(reader)) {
      expect(chunk).toBe('a');
      break;
    }
    expect(state.cancelled).toBe(true);
  });

  it('readTextChunks still drains a closed stream (cancel after close is harmless)', async () => {
    const encoder = new TextEncoder();
    const body = new ReadableStream<Uint8Array>({
      start(controller) {
        controller.enqueue(encoder.encode('x'));
        controller.close();
      },
    });
    const out: string[] = [];
    for await (const chunk of readTextChunks(body.getReader())) out.push(chunk);
    expect(out).toEqual(['x']);
  });

  it('chatStream releases the body when the consumer breaks early', async () => {
    const { body, state } = trackedBody(['data: one\n\n', 'data: two\n\n']);
    const { client } = scripted([() => new Response(body)]);
    for await (const chunk of client.chatStream('q')) {
      expect(chunk).toBe('one');
      break;
    }
    expect(state.cancelled).toBe(true);
  });

  it('streamRunEvents releases the body when the consumer breaks early', async () => {
    const frame = 'id: e1\nevent: thought\ndata: {"type":"thought"}\n\n';
    const { body, state } = trackedBody([frame, frame]);
    const { client } = scripted([() => new Response(body)]);
    for await (const event of client.streamRunEvents('r1')) {
      expect(event.type).toBe('thought');
      break;
    }
    expect(state.cancelled).toBe(true);
  });
});

import { describe, it, expect } from 'vitest';
import {
  BaselithClient,
  BaselithConfigError,
  NotFoundError,
  RunTimeoutError,
  iterPages,
} from '../src/index.js';

const BASE = 'https://api.test';

interface Call {
  url: string;
  method: string;
  headers: Record<string, string>;
  body: unknown;
}

function json(payload: unknown, status = 200, headers: Record<string, string> = {}): Response {
  return new Response(JSON.stringify(payload), {
    status,
    headers: { 'content-type': 'application/json', ...headers },
  });
}

/** A client whose fetch replays `responses` in order (the last one repeats). */
function recording(responses: Array<() => Response>, opts: Record<string, unknown> = {}) {
  const calls: Call[] = [];
  const queue = [...responses];
  const client = new BaselithClient({
    baseUrl: BASE,
    apiKey: 'k',
    maxRetries: 0,
    fetchImpl: (url, init) => {
      calls.push({
        url,
        method: init?.method ?? 'GET',
        headers: (init?.headers ?? {}) as Record<string, string>,
        body: init?.body ? JSON.parse(String(init.body)) : undefined,
      });
      const next = queue.length > 1 ? queue.shift()! : queue[0]!;
      return Promise.resolve(next());
    },
    ...opts,
  });
  return { client, calls };
}

function page<K extends string>(key: K, items: unknown[], next: string | null = null) {
  return { [key]: items, count: items.length, next_cursor: next, has_more: next !== null };
}

const ENDPOINT = {
  id: 'whe_1',
  url: 'https://hooks.example.com/x',
  event_types: ['*'],
  enabled: true,
  has_secret: true,
};
const DELIVERY = {
  id: 'whd_1',
  endpoint_id: 'whe_1',
  event_id: 'evt_1',
  event_type: 'agent.completed',
  status: 'failed',
  attempts: 3,
};

describe('auth', () => {
  it('sends HTTP Basic credentials', async () => {
    const { client, calls } = recording([() => json(page('pending', []))], {
      basicAuth: { username: 'admin', password: 's3cret' },
    });
    await client.listApprovals();
    expect(calls[0]!.headers['Authorization']).toBe(`Basic ${btoa('admin:s3cret')}`);
  });

  it('rejects bearer + basic together', () => {
    expect(
      () =>
        new BaselithClient({
          baseUrl: BASE,
          bearerToken: 't',
          basicAuth: { username: 'a', password: 'b' },
        })
    ).toThrow(BaselithConfigError);
  });
});

describe('async agent runs', () => {
  it('submits a run and reads Location', async () => {
    const { client, calls } = recording([
      () =>
        json({ task_id: 't1', status_url: '/v1/agent/status/t1' }, 202, {
          Location: '/v1/agent/status/t1',
        }),
    ]);
    const sub = await client.submitAgentRun('hello', { conversation_id: 'c1' });
    expect(calls[0]!.method).toBe('POST');
    expect(calls[0]!.url).toBe(`${BASE}/v1/agent/async`);
    expect(calls[0]!.body).toEqual({ query: 'hello', conversation_id: 'c1' });
    expect(calls[0]!.headers['Idempotency-Key']).toBeTruthy();
    expect(sub).toMatchObject({ task_id: 't1', location: '/v1/agent/status/t1' });
  });

  it('percent-encodes path params', async () => {
    const { client, calls } = recording([() => json({ status: 'running' })]);
    const st = await client.getAgentRun('a/b?c');
    expect(calls[0]!.url).toBe(`${BASE}/v1/agent/status/a%2Fb%3Fc`);
    expect(st.status).toBe('running');
  });

  it('waitForRun polls until terminal', async () => {
    const { client, calls } = recording([
      () => json({ status: 'queued' }),
      () => json({ status: 'running' }),
      () => json({ status: 'completed', result: { answer: '42' } }),
    ]);
    const st = await client.waitForRun('t1', { timeoutMs: 5000, pollIntervalMs: 1 });
    expect(calls).toHaveLength(3);
    expect(st.result).toEqual({ answer: '42' });
  });

  it('waitForRun throws RunTimeoutError', async () => {
    const { client } = recording([() => json({ status: 'running' })]);
    const err = await client
      .waitForRun('t1', { timeoutMs: 0, pollIntervalMs: 1 })
      .catch((e: unknown) => e);
    expect(err).toBeInstanceOf(RunTimeoutError);
    expect((err as RunTimeoutError).taskId).toBe('t1');
    expect((err as RunTimeoutError).lastStatus).toEqual({ status: 'running' });
  });

  it('waitForRun rejects a non-positive interval', async () => {
    const { client } = recording([() => json({ status: 'running' })]);
    await expect(client.waitForRun('t1', { pollIntervalMs: 0 })).rejects.toBeInstanceOf(
      BaselithConfigError
    );
  });

  it('maps problem+json on new routes', async () => {
    const { client } = recording([
      () =>
        new Response(
          JSON.stringify({
            type: 'urn:baselith:error:not_found',
            title: 'Not Found',
            status: 404,
            detail: 'unknown task id',
            code: 'not_found',
            request_id: 'req-9',
          }),
          { status: 404, headers: { 'content-type': 'application/problem+json' } }
        ),
    ]);
    const err = (await client.getAgentRun('nope').catch((e: unknown) => e)) as NotFoundError;
    expect(err).toBeInstanceOf(NotFoundError);
    expect(err.code).toBe('not_found');
    expect(err.requestId).toBe('req-9');
    expect(err.message).toBe('unknown task id');
  });
});

const EVENTS = [
  ': keepalive\n\nid: e1\nevent: run_started\ndata: {"type": "run_started"}\n\n',
  ': keepalive\n\nid: e2\nevent: thou',
  'ght\ndata: {"type": "thought", "content": "hmm"}\n\n',
  'id: e3\nevent: final\ndata: {"type": "final", "content": "done"}\n\n',
  'id: e4\nevent: thought\ndata: {"type": "thought", "content": "late"}\n\n',
];

function sse(chunks: string[]): Response {
  const encoder = new TextEncoder();
  return new Response(
    new ReadableStream<Uint8Array>({
      start(controller) {
        for (const c of chunks) controller.enqueue(encoder.encode(c));
        controller.close();
      },
    }),
    { headers: { 'content-type': 'text/event-stream' } }
  );
}

describe('run events', () => {
  it('streams typed events with ids and stops at the terminal one', async () => {
    const { client, calls } = recording([() => sse(EVENTS)]);
    const events = [];
    for await (const ev of client.streamRunEvents('r1', { lastEventId: 'e0' })) events.push(ev);
    expect(calls[0]!.url).toBe(`${BASE}/v1/runs/r1/events`);
    expect(calls[0]!.method).toBe('GET');
    expect(calls[0]!.headers['Accept']).toBe('text/event-stream');
    expect(calls[0]!.headers['Last-Event-ID']).toBe('e0');
    expect(events.map((e) => e.id)).toEqual(['e1', 'e2', 'e3']);
    expect(events.map((e) => e.type)).toEqual(['run_started', 'thought', 'final']);
    expect(events[1]!.content).toBe('hmm');
  });

  it('yields event: error as a terminal run event', async () => {
    const { client } = recording([
      () => sse(['id: x\nevent: error\ndata: {"type": "error", "content": "boom"}\n\n']),
    ]);
    const events = [];
    for await (const ev of client.streamRunEvents('r1')) events.push(ev);
    expect(events).toHaveLength(1);
    expect(events[0]).toMatchObject({ id: 'x', type: 'error', content: 'boom' });
  });

  it('throws the typed error on an HTTP failure', async () => {
    const { client } = recording([() => json({ detail: 'nope' }, 404)]);
    const gen = client.streamRunEvents('r1');
    await expect(gen.next()).rejects.toBeInstanceOf(NotFoundError);
  });

  it('pages run history with limit + cursor', async () => {
    const { client, calls } = recording([
      () => json({ run_id: 'r1', ...page('history', [{ version: 1 }], 'c2') }),
    ]);
    const hp = await client.getRunHistory('r1', { limit: 5, cursor: 'c1' });
    expect(calls[0]!.url).toBe(`${BASE}/v1/runs/r1/history?limit=5&cursor=c1`);
    expect(hp.history).toEqual([{ version: 1 }]);
    expect(hp.next_cursor).toBe('c2');
  });
});

describe('approvals', () => {
  it('lists with filters, omitting unset params', async () => {
    const { client, calls } = recording([
      () => json(page('pending', [{ run_id: 'r1', pending_approval: {} }])),
    ]);
    const ap = await client.listApprovals({ tenantId: 'acme' });
    expect(calls[0]!.url).toBe(`${BASE}/v1/approvals?tenant_id=acme`);
    expect(ap.pending[0]!.run_id).toBe('r1');
  });

  it('records a decision and resumes', async () => {
    const { client, calls } = recording([
      () => json({ run_id: 'r1', recorded: true, approved: false }),
      () => json({ run_id: 'r1', result: { answer: 'ok' } }),
    ]);
    const dec = await client.decideApproval('r1', { approved: false, reason: 'too risky' });
    const res = await client.resumeRun('r1');
    expect(calls[0]!.url).toBe(`${BASE}/v1/approvals/r1/decision`);
    expect(calls[0]!.body).toEqual({ approved: false, reason: 'too risky' });
    expect(dec.recorded).toBe(true);
    expect(calls[1]!.method).toBe('POST');
    expect(calls[1]!.url).toBe(`${BASE}/v1/approvals/r1/resume`);
    expect(res.result).toEqual({ answer: 'ok' });
  });
});

describe('webhooks', () => {
  it('creates with the all-events default', async () => {
    const { client, calls } = recording([
      () => json({ endpoint: ENDPOINT, secret: 'whsec_x' }, 201),
    ]);
    const created = await client.createWebhook({ url: ENDPOINT.url });
    expect(calls[0]!.method).toBe('POST');
    expect(calls[0]!.url).toBe(`${BASE}/v1/webhooks`);
    expect(calls[0]!.body).toEqual({ event_types: ['*'], url: ENDPOINT.url });
    expect(calls[0]!.headers['Idempotency-Key']).toBeTruthy();
    expect(created.secret).toBe('whsec_x');
  });

  it('lists, deletes, lists deliveries and replays', async () => {
    const { client, calls } = recording([
      () => json(page('endpoints', [ENDPOINT])),
      () => json({ status: 'deleted', endpoint_id: 'whe_1' }),
      () => json(page('deliveries', [DELIVERY], 'n1')),
      () => json({ status: 'success', delivery: { ...DELIVERY, status: 'success' } }),
    ]);
    const wp = await client.listWebhooks({ limit: 10 });
    const del = await client.deleteWebhook('whe_1');
    const dp = await client.listWebhookDeliveries();
    const rep = await client.replayWebhookDelivery('whd_1');
    expect(calls[0]!.url).toBe(`${BASE}/v1/webhooks?limit=10`);
    expect(wp.endpoints[0]!.id).toBe('whe_1');
    expect(calls[1]!.method).toBe('DELETE');
    expect(calls[1]!.url).toBe(`${BASE}/v1/webhooks/whe_1`);
    expect(del.status).toBe('deleted');
    expect(calls[2]!.url).toBe(`${BASE}/v1/webhooks/deliveries`);
    expect(dp.has_more).toBe(true);
    expect(calls[3]!.url).toBe(`${BASE}/v1/webhooks/deliveries/whd_1/replay`);
    expect(rep.delivery.status).toBe('success');
  });
});

describe('pagination', () => {
  it('iterPages follows next_cursor', async () => {
    const { client, calls } = recording([
      () => json(page('endpoints', [ENDPOINT], 'c2')),
      () => json(page('endpoints', [{ ...ENDPOINT, id: 'whe_2' }], 'c3')),
      () => json(page('endpoints', [{ ...ENDPOINT, id: 'whe_3' }])),
    ]);
    const ids: string[] = [];
    for await (const p of client.iterPages((o) => client.listWebhooks(o), { limit: 1 }))
      ids.push(...p.endpoints.map((e) => e.id));
    expect(ids).toEqual(['whe_1', 'whe_2', 'whe_3']);
    expect(calls.map((c) => c.url)).toEqual([
      `${BASE}/v1/webhooks?limit=1`,
      `${BASE}/v1/webhooks?limit=1&cursor=c2`,
      `${BASE}/v1/webhooks?limit=1&cursor=c3`,
    ]);
  });

  it('standalone iterPages stops on a repeated cursor', async () => {
    const { client, calls } = recording([() => json(page('deliveries', [DELIVERY], 'same'))]);
    const pages = [];
    for await (const p of iterPages((o) => client.listWebhookDeliveries(o), { cursor: 'same' }))
      pages.push(p);
    expect(pages).toHaveLength(1);
    expect(calls).toHaveLength(1);
  });
});

/* Tests for the console's fetch-based API client, focused on the
 * `/chat/stream` SSE decoding in streamChat() (Task 16).
 *
 * No test runner is wired up for this vanilla frontend module elsewhere in
 * the repo (no package.json, no CI job) — these run with Node's built-in
 * test runner, which needs nothing installed:
 *
 *   node --test core/static/frontend/js/api.test.mjs
 */

import test from 'node:test';
import assert from 'node:assert/strict';

import { streamChat, ApiError } from './api.js';

/** A `Response.body.getReader()`-alike that replays fixed byte chunks. */
function fakeReader(chunks) {
  const encoder = new TextEncoder();
  const queue = chunks.map((c) => encoder.encode(c));
  let i = 0;
  return {
    async read() {
      if (i >= queue.length) return { done: true, value: undefined };
      return { done: false, value: queue[i++] };
    },
  };
}

/** A minimal fake `Response` carrying a streaming body of `chunks`. */
function fakeStreamResponse(chunks) {
  return {
    ok: true,
    status: 200,
    headers: { get: () => null },
    body: { getReader: () => fakeReader(chunks) },
    json: async () => ({}),
    text: async () => '',
  };
}

function withFakeFetch(response, run) {
  const original = globalThis.fetch;
  globalThis.fetch = async () => response;
  return run().finally(() => {
    globalThis.fetch = original;
  });
}

test('streamChat decodes SSE data frames into text chunks and stops at event: done', async () => {
  const res = fakeStreamResponse(['data: Hello\n\ndata:  world\n\n', 'event: done\ndata: [DONE]\n\n']);
  const chunks = [];
  const used = await withFakeFetch(res, () => streamChat({ query: 'q' }, (c) => chunks.push(c)));
  assert.equal(used, true);
  // Two separate model chunks, "Hello" and " world" (leading space kept —
  // only one space after "data:" is stripped, per the SSE spec).
  assert.deepEqual(chunks, ['Hello', ' world']);
  assert.equal(chunks.join(''), 'Hello world');
});

test('streamChat reassembles an SSE frame split across arbitrary chunk boundaries', async () => {
  // The blank line ending the first event, and the "done" keyword itself,
  // are each split across two simulated network reads.
  const res = fakeStreamResponse(['data: hel', 'lo\n', '\nevent: don', 'e\ndata: [DONE]\n\n']);
  const chunks = [];
  const used = await withFakeFetch(res, () => streamChat({ query: 'q' }, (c) => chunks.push(c)));
  assert.equal(used, true);
  assert.deepEqual(chunks, ['hello']);
});

test('streamChat merges a CRLF line split exactly at the \\r/\\n boundary (regression)', async () => {
  // A chunk boundary between "\r" and its "\n" must not fragment one logical
  // `data:` block into two.
  const res = fakeStreamResponse(['data: hello\r', '\ndata: world\r\n\r\n']);
  const chunks = [];
  const used = await withFakeFetch(res, () => streamChat({ query: 'q' }, (c) => chunks.push(c)));
  assert.equal(used, true);
  assert.deepEqual(chunks, ['hello\nworld']);
});

test('streamChat tolerates keepalive comments and reassembles multi-line data fields', async () => {
  const res = fakeStreamResponse([
    ': keepalive\n\n',
    'data: line1\ndata: line2\n\n',
    ': keepalive\n\n',
    'event: done\ndata: [DONE]\n\n',
  ]);
  const chunks = [];
  const used = await withFakeFetch(res, () => streamChat({ query: 'q' }, (c) => chunks.push(c)));
  assert.equal(used, true);
  assert.deepEqual(chunks, ['line1\nline2']);
});

test('streamChat surfaces event: error as an ApiError instead of leaking it as text', async () => {
  const res = fakeStreamResponse([
    'data: partial\n\n',
    'event: error\ndata: stream failed\n\n',
    'event: done\ndata: [DONE]\n\n',
  ]);
  const chunks = [];
  await assert.rejects(
    () => withFakeFetch(res, () => streamChat({ query: 'q' }, (c) => chunks.push(c))),
    (err) => {
      assert.ok(err instanceof ApiError);
      assert.equal(err.message, 'stream failed');
      return true;
    }
  );
  assert.deepEqual(chunks, ['partial']);
});

test('streamChat returns false (caller falls back) when the body cannot be streamed', async () => {
  const res = {
    ok: true,
    status: 200,
    headers: { get: () => null },
    body: null,
  };
  const chunks = [];
  const used = await withFakeFetch(res, () => streamChat({ query: 'q' }, (c) => chunks.push(c)));
  assert.equal(used, false);
  assert.deepEqual(chunks, []);
});

/**
 * Server-Sent Events decoding for `/chat/stream` and `/runs/{run_id}/events`.
 *
 * Split out of `client.ts` (which holds the API methods the `sdk-contract`
 * gate reads): one incremental decoder that ignores `: keepalive` comments,
 * reassembles multi-line `data:` fields and reads `id:`, shared by the chat
 * text stream and the structured run-event stream.
 */

import type { RunEvent } from './models.js';

/**
 * Raised when `/chat/stream` frames an `event: error` mid-stream.
 *
 * The server has already committed to a 200 response by the time a provider
 * read fails, so the only way to report it is in-band (see
 * `plugins/api_routers/chat.py`'s `sse_error_event`). The message is
 * deliberately generic — the server never puts provider detail on the wire.
 * Current servers send a JSON payload whose `code` and `requestId` (quote it
 * when reporting the failure) are exposed here; older servers sent the bare
 * text `stream failed`, which still parses (both `undefined`).
 */
export class ChatStreamError extends Error {
  readonly code?: string;
  readonly requestId?: string;

  constructor(message = 'stream failed', opts: { code?: string; requestId?: string } = {}) {
    super(message);
    this.name = new.target.name;
    this.code = opts.code;
    this.requestId = opts.requestId;
  }

  /** Build from an `event: error` payload: a JSON object or legacy text. */
  static fromEventData(data: string): ChatStreamError {
    if (data.trimStart().startsWith('{')) {
      try {
        const payload = JSON.parse(data) as Record<string, unknown>;
        if (payload && typeof payload === 'object') {
          const detail = payload.detail ?? payload.message ?? 'stream failed';
          return new ChatStreamError(String(detail), {
            code: typeof payload.code === 'string' ? payload.code : undefined,
            requestId: typeof payload.request_id === 'string' ? payload.request_id : undefined,
          });
        }
      } catch {
        // fall through: not JSON, treat as legacy text
      }
    }
    return new ChatStreamError(data || 'stream failed');
  }
}

/** One decoded SSE event: optional `event:` name and `id:`, plus its `data:` payload. */
interface SseEvent {
  event: string | null;
  id: string | null;
  data: string;
}

/**
 * Parse one blank-line-delimited SSE block into an event.
 *
 * Returns `null` for a block that carries no event at all — e.g. one made
 * only of `: keepalive`-style comment lines.
 */
function parseSseBlock(block: string): SseEvent | null {
  let event: string | null = null;
  let id: string | null = null;
  const dataLines: string[] = [];
  for (const line of block.split('\n')) {
    if (!line || line.startsWith(':')) continue; // blank line, or a comment (keepalive)
    if (line.startsWith('event:')) {
      event = line.slice('event:'.length).trim();
    } else if (line.startsWith('id:')) {
      id = line.slice('id:'.length).trim();
    } else if (line.startsWith('data:')) {
      let value = line.slice('data:'.length);
      if (value.startsWith(' ')) value = value.slice(1);
      dataLines.push(value);
    }
  }
  if (event === null && dataLines.length === 0) return null;
  return { event, id, data: dataLines.join('\n') };
}

/**
 * Incremental text -> SSE-event decoder.
 *
 * Buffers raw text across chunk boundaries (a `data:` line, or the blank
 * line ending an event, can arrive split across two reads) and yields one
 * {@link SseEvent} per complete, blank-line-terminated block.
 */
class SseDecoder {
  private buffer = '';
  // A chunk boundary can fall exactly between a "\r" and its "\n": normalising
  // a trailing "\r" on the spot would make a "\n" arriving at the start of the
  // *next* feed() read as a second, spurious line break instead of completing
  // the same "\r\n". So a trailing bare "\r" is held back (not normalised yet)
  // until the next feed() or flush() resolves it, one way or the other.
  private pendingCr = false;

  *feed(text: string): Generator<SseEvent> {
    if (this.pendingCr) {
      text = '\r' + text;
      this.pendingCr = false;
    }
    if (text.endsWith('\r')) {
      text = text.slice(0, -1);
      this.pendingCr = true;
    }
    this.buffer += text.replace(/\r\n/g, '\n').replace(/\r/g, '\n');
    let idx: number;
    while ((idx = this.buffer.indexOf('\n\n')) !== -1) {
      const block = this.buffer.slice(0, idx);
      this.buffer = this.buffer.slice(idx + 2);
      const event = parseSseBlock(block);
      if (event) yield event;
    }
  }

  /** Yield one final event from a trailing, unterminated buffer (if any). */
  *flush(): Generator<SseEvent> {
    if (this.pendingCr) {
      this.buffer += '\n';
      this.pendingCr = false;
    }
    if (this.buffer.trim()) {
      const event = parseSseBlock(this.buffer);
      this.buffer = '';
      if (event) yield event;
    }
  }
}

/**
 * Decode a raw `/chat/stream` text stream into plain text chunks.
 *
 * Frames the wire format emitted by `plugins/api_routers/chat.py`: splits on
 * blank lines, reassembles multi-line `data:` fields, ignores `: keepalive`
 * comment lines, stops at `event: done` and throws {@link ChatStreamError} on
 * `event: error`.
 */
export async function* decodeSseStream(
  rawChunks: AsyncIterable<string> | Iterable<string>
): AsyncGenerator<string> {
  const sse = new SseDecoder();
  for await (const raw of rawChunks) {
    for (const event of sse.feed(raw)) {
      if (event.event === 'done') return;
      if (event.event === 'error') throw ChatStreamError.fromEventData(event.data);
      yield event.data;
    }
  }
  for (const event of sse.flush()) {
    if (event.event === 'done') return;
    if (event.event === 'error') throw ChatStreamError.fromEventData(event.data);
    yield event.data;
  }
}

/**
 * Yield decoded text chunks from a `ReadableStream<Uint8Array>` reader.
 *
 * The reader is always cancelled on exit — including when the consumer stops
 * early (`break`/`return` out of a `for await`, or a thrown error) — so the
 * response body is released and the underlying connection is closed instead
 * of being left locked and open.
 */
export async function* readTextChunks(
  reader: ReadableStreamDefaultReader<Uint8Array>
): AsyncGenerator<string> {
  const decoder = new TextDecoder();
  try {
    for (;;) {
      const { done, value } = await reader.read();
      if (done) break;
      if (value) yield decoder.decode(value, { stream: true });
    }
    const tail = decoder.decode();
    if (tail) yield tail;
  } finally {
    try {
      await reader.cancel();
    } catch {
      // already closed or errored: nothing left to release
    }
  }
}

/** `AgentEvent` types after which the server closes a run's event stream. */
export const TERMINAL_EVENT_TYPES: ReadonlySet<string> = new Set(['final', 'error', 'human']);

/**
 * Build a {@link RunEvent} from one `/runs/{run_id}/events` frame
 * (`id: <AgentEvent.id>` + `event: <type>` + `data: <AgentEvent JSON>`).
 * Unlike `/chat/stream`, `event: error` here is an ordinary terminal run
 * event, not a transport failure, so it is yielded rather than thrown.
 */
function toRunEvent(event: SseEvent): RunEvent {
  let payload: Record<string, unknown> = {};
  if (event.data) {
    try {
      const parsed: unknown = JSON.parse(event.data);
      payload =
        parsed && typeof parsed === 'object' && !Array.isArray(parsed)
          ? (parsed as Record<string, unknown>)
          : { content: event.data };
    } catch {
      payload = { content: event.data };
    }
  }
  const type = typeof payload.type === 'string' ? payload.type : (event.event ?? 'message');
  const out: RunEvent = { ...payload, type, data: (payload.data as RunEvent['data']) ?? {} };
  if (event.id !== null) out.id = event.id;
  return out;
}

/** Decode a raw run-event SSE stream into {@link RunEvent}s; stops after a terminal event. */
export async function* decodeRunEvents(
  rawChunks: AsyncIterable<string> | Iterable<string>
): AsyncGenerator<RunEvent> {
  const sse = new SseDecoder();
  for await (const raw of rawChunks) {
    for (const event of sse.feed(raw)) {
      const runEvent = toRunEvent(event);
      yield runEvent;
      if (TERMINAL_EVENT_TYPES.has(runEvent.type)) return;
    }
  }
  for (const event of sse.flush()) yield toRunEvent(event);
}

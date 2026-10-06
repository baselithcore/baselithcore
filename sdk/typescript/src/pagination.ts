/**
 * Cursor pagination and run-polling helpers.
 *
 * Every list endpoint answers `{<items>, count, next_cursor, has_more}` and
 * takes `limit` / `cursor` query parameters; {@link iterPages} follows
 * `next_cursor` until the last page. {@link pollUntilTerminal} backs
 * `BaselithClient.waitForRun`.
 */

import { BaselithConfigError, RunTimeoutError } from './errors.js';
import type { AgentRunStatus, Page } from './models.js';

/** Options every list method accepts. */
export interface PageOptions {
  limit?: number;
  cursor?: string | null;
}

/**
 * Yield every page of a cursor-paginated listing, following `next_cursor`.
 *
 * @param fetch A list method bound to its client, e.g.
 *   `(o) => client.listWebhooks(o)` — anything taking `{ cursor }` and
 *   returning a {@link Page}.
 * @param opts Passed to every call (`limit`, filters). A `cursor` here starts
 *   from that cursor instead of the first page.
 */
export async function* iterPages<O extends PageOptions, P extends Page>(
  fetch: (opts: O) => Promise<P>,
  opts: O = {} as O
): AsyncGenerator<P> {
  let cursor = opts.cursor ?? null;
  for (;;) {
    const page = await fetch({ ...opts, cursor });
    yield page;
    if (!page.has_more || !page.next_cursor || page.next_cursor === cursor) return;
    cursor = page.next_cursor;
  }
}

/** Task states after which a queued run's status no longer changes. */
export const TERMINAL_RUN_STATUSES: ReadonlySet<string> = new Set([
  'completed',
  'failed',
  'cancelled',
]);

const sleep = (ms: number): Promise<void> => new Promise((resolve) => setTimeout(resolve, ms));

/** Poll `getStatus(taskId)` until terminal; {@link RunTimeoutError} after `timeoutMs`. */
export async function pollUntilTerminal(
  getStatus: (taskId: string) => Promise<AgentRunStatus>,
  taskId: string,
  timeoutMs: number,
  pollIntervalMs: number
): Promise<AgentRunStatus> {
  if (timeoutMs < 0) throw new BaselithConfigError('timeoutMs must be >= 0');
  if (pollIntervalMs <= 0) throw new BaselithConfigError('pollIntervalMs must be > 0');
  const deadline = Date.now() + timeoutMs;
  for (;;) {
    const status = await getStatus(taskId);
    if (TERMINAL_RUN_STATUSES.has(status.status)) return status;
    const remaining = deadline - Date.now();
    if (remaining <= 0) throw new RunTimeoutError(taskId, timeoutMs, status);
    await sleep(Math.min(pollIntervalMs, remaining));
  }
}

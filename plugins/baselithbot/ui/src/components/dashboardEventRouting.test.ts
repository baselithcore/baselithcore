import { describe, expect, it } from 'vitest';
import type { DashboardEvent } from '../lib/api';
import { collectInvalidations } from './dashboardEventRouting';

const ev = (type: string, payload: Record<string, unknown> = {}) =>
  ({ type, payload, ts: 0 }) as unknown as DashboardEvent;

describe('collectInvalidations', () => {
  it('deduplicates keys across a burst of run.step events', () => {
    const burst = Array.from({ length: 50 }, () => ev('run.step', { run_id: 'r1' }));
    const keys = collectInvalidations(burst);
    expect(keys).toContainEqual(['runTaskById', 'r1']);
    expect(keys).toContainEqual(['runTaskLatest']);
    expect(keys.length).toBe(new Set(keys.map((k) => JSON.stringify(k))).size);
    expect(keys.length).toBe(5);
  });

  it('routes session and approval events', () => {
    const keys = collectInvalidations([
      ev('session.created', { id: 's1' }),
      ev('approval.pending'),
    ]);
    expect(keys).toContainEqual(['sessions']);
    expect(keys).toContainEqual(['sessionHistory', 's1']);
    expect(keys).toContainEqual(['overview']);
    expect(keys).toContainEqual(['approvals']);
  });

  it('returns nothing for unknown types', () => {
    expect(collectInvalidations([ev('something.else')])).toEqual([]);
  });
});

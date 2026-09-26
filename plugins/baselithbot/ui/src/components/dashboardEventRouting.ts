import type { DashboardEvent } from '../lib/api';

// Maps a dashboard event to the react-query keys it makes stale. Kept apart
// from DashboardProvider so the provider stays a thin transport layer and the
// routing table can be unit-tested without an EventSource.

const OVERVIEW_REFRESH_TYPES = new Set<string>([
  'session.created',
  'session.reset',
  'session.deleted',
  'skill.clawhub_synced',
  'skill.installed',
  'skill.rescanned',
  'skill.removed',
  'cron.removed',
  'cron.custom_registered',
  'cron.custom_updated',
  'node.token_issued',
  'node.revoked',
  'workspace.created',
  'workspace.updated',
  'workspace.deleted',
  'agent.custom_registered',
  'agent.custom_updated',
  'agent.custom_deleted',
  'channel.started',
  'channel.stopped',
  'channel.config_updated',
  'channel.config_deleted',
  'channel.inbound',
  'canvas.rendered',
  'canvas.cleared',
  'provider_keys.updated',
  'provider_keys.deleted',
]);

const SKILL_REFRESH_TYPES = new Set<string>([
  'skill.clawhub_configured',
  'skill.clawhub_synced',
  'skill.installed',
  'skill.rescanned',
  'skill.removed',
]);

const RUNTIME_REFRESH_TYPES = new Set<string>(['computer_use.updated', 'stealth.updated']);
const APPROVAL_REFRESH_TYPES = new Set<string>([
  'approval.pending',
  'approval.resolved',
  'approval.approved',
  'approval.denied',
]);

function readSessionId(parsed: DashboardEvent): string | null {
  const payload = parsed.payload;
  if (!payload || typeof payload !== 'object') return null;
  if ('session_id' in payload && payload.session_id != null) {
    return String(payload.session_id);
  }
  if (parsed.type === 'session.created' && 'id' in payload && payload.id != null) {
    return String(payload.id);
  }
  return null;
}

function readRunId(parsed: DashboardEvent): string {
  const payload = parsed.payload;
  return payload && typeof payload === 'object' && 'run_id' in payload
    ? String(payload.run_id)
    : '';
}

/**
 * Collect the query keys a batch of events invalidates, deduplicated.
 *
 * A browser run emits one `run.step` per action; invalidating per event
 * cancelled and restarted the same four fetches dozens of times a second.
 * Returning a de-duplicated set lets the caller invalidate each key once
 * per flush window.
 */
export function collectInvalidations(events: readonly DashboardEvent[]): unknown[][] {
  const keys = new Map<string, unknown[]>();
  const add = (key: unknown[]) => keys.set(JSON.stringify(key), key);

  for (const parsed of events) {
    const type = typeof parsed.type === 'string' ? parsed.type : '';
    if (OVERVIEW_REFRESH_TYPES.has(type)) add(['overview']);
    if (SKILL_REFRESH_TYPES.has(type)) add(['skills']);
    if (RUNTIME_REFRESH_TYPES.has(type)) {
      add(['overview']);
      add(['computer-use']);
      add(['stealth']);
      add(['audit-log']);
      add(['approvals']);
    }
    if (APPROVAL_REFRESH_TYPES.has(type)) add(['approvals']);
    if (type.startsWith('session.')) {
      add(['sessions']);
      const sessionId = readSessionId(parsed);
      if (sessionId) add(['sessionHistory', sessionId]);
    }
    if (type.startsWith('run.')) {
      add(['runTaskLatest']);
      add(['runTaskRecent']);
      add(['replay-runs']);
      add(['replay-run']);
      const runId = readRunId(parsed);
      if (runId) add(['runTaskById', runId]);
    }
  }
  return Array.from(keys.values());
}

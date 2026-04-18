import type { DashboardEvent, Session, SessionMessage } from '../../lib/api';

export type SortKey = 'activity' | 'title' | 'created';

export interface SessionRunHint {
  runId: string;
  status: string | null;
  kind: string | null;
}

export function sortSessions(sessions: Session[], sort: SortKey): Session[] {
  return [...sessions].sort((left, right) => {
    if (sort === 'title') {
      return (left.title || left.id).localeCompare(right.title || right.id);
    }
    if (sort === 'created') {
      return right.created_at - left.created_at || right.last_active - left.last_active;
    }
    return right.last_active - left.last_active || right.created_at - left.created_at;
  });
}

export function pickDefaultSessionId(sessions: Session[]): string | null {
  return sortSessions(sessions, 'activity')[0]?.id ?? null;
}

export function extractLatestRun(messages: SessionMessage[]): SessionRunHint | null {
  for (let index = messages.length - 1; index >= 0; index -= 1) {
    const meta = messages[index]?.metadata || {};
    const runId = typeof meta.run_id === 'string' ? meta.run_id : null;
    if (!runId) continue;
    return {
      runId,
      status: typeof meta.status === 'string' ? meta.status : null,
      kind: typeof meta.kind === 'string' ? meta.kind : null,
    };
  }
  return null;
}

export function readEventSessionId(event: DashboardEvent): string | null {
  const payload = event.payload;
  if (!payload || typeof payload !== 'object') return null;
  if ('session_id' in payload && payload.session_id != null) {
    return String(payload.session_id);
  }
  if (event.type === 'session.created' && 'id' in payload && payload.id != null) {
    return String(payload.id);
  }
  return null;
}

export function badgeTone(status: string): 'ok' | 'warn' | 'err' | 'muted' {
  if (status === 'completed') return 'ok';
  if (status === 'running' || status === 'connecting') return 'warn';
  if (status === 'failed' || status === 'error') return 'err';
  return 'muted';
}

export function roleTone(role: string): 'user' | 'assistant' | 'system' {
  if (role === 'user') return 'user';
  if (role === 'assistant' || role === 'bot') return 'assistant';
  return 'system';
}

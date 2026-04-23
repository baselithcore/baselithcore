import type { ApprovalStatus } from '../../lib/api';
import { truncate } from '../../lib/format';

export type DecisionInput = {
  id: string;
  verdict: 'approve' | 'deny';
  reason: string;
};

export function statusTone(status: ApprovalStatus): 'ok' | 'warn' | 'err' | 'muted' {
  switch (status) {
    case 'approved':
      return 'ok';
    case 'denied':
      return 'err';
    case 'timed_out':
      return 'warn';
    default:
      return 'muted';
  }
}

export function formatCapability(capability: string): string {
  return capability.replace(/_/g, ' ');
}

export function formatDuration(seconds: number | null | undefined): string {
  if (!seconds || !Number.isFinite(seconds) || seconds <= 0) return '—';
  if (seconds < 60) return `${seconds.toFixed(0)}s`;
  if (seconds < 3600) return `${(seconds / 60).toFixed(seconds >= 600 ? 0 : 1)}m`;
  return `${(seconds / 3600).toFixed(seconds >= 36_000 ? 0 : 1)}h`;
}

export function summarizeParams(params: Record<string, unknown>): string {
  if (Array.isArray(params.argv) && params.argv.length > 0) {
    return params.argv.map((value) => String(value)).join(' ');
  }
  if (typeof params.path === 'string' && params.path) return params.path;
  if (typeof params.text === 'string' && params.text) return truncate(params.text, 120);

  const entries = Object.entries(params);
  if (entries.length === 0) return 'No params payload';

  return entries
    .slice(0, 3)
    .map(([key, value]) => {
      if (typeof value === 'string') return `${key}=${truncate(value, 36)}`;
      return `${key}=${truncate(JSON.stringify(value), 36)}`;
    })
    .join(' · ');
}

export function countdownLabel(expiresAt: number, now: number): string {
  const remaining = Math.max(0, expiresAt - now);
  return remaining <= 0 ? 'expired' : `${remaining.toFixed(0)}s left`;
}

export function topEntries(source: Record<string, number>, limit = 5): Array<[string, number]> {
  return Object.entries(source)
    .sort((left, right) => right[1] - left[1] || left[0].localeCompare(right[0]))
    .slice(0, limit);
}

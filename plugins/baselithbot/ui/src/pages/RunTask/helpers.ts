import type { RunTaskState } from '../../lib/api';

export const URL_REGEX = /^https?:\/\/[^\s<>"]+$/i;
export const EMAIL_REGEX = /^[^\s@]+@[^\s@]+\.[^\s@]+$/i;
export const GITHUB_REPO_REGEX = /^([A-Za-z0-9][\w.-]*)\s*\/\s*([\w.-]+)$/;

export function detectLink(raw: string): { href: string; label: string } | null {
  const trimmed = raw.trim();
  if (!trimmed) return null;
  if (URL_REGEX.test(trimmed)) {
    return { href: trimmed, label: trimmed };
  }
  if (EMAIL_REGEX.test(trimmed)) {
    return { href: `mailto:${trimmed}`, label: trimmed };
  }
  const repo = trimmed.match(GITHUB_REPO_REGEX);
  if (repo) {
    const [, org, name] = repo;
    return {
      href: `https://github.com/${org}/${name}`,
      label: `${org} / ${name}`,
    };
  }
  return null;
}

export function badgeTone(status: RunTaskState['status']) {
  if (status === 'completed') return 'ok';
  if (status === 'failed') return 'err';
  return 'warn';
}

export function describeRunEvent(event: { type: string; payload: Record<string, unknown> }) {
  if (event.type === 'run.started') {
    return `run started · ${String(event.payload.goal ?? '')}`;
  }
  if (event.type === 'run.step') {
    return `${String(event.payload.action ?? 'step')} · ${String(event.payload.reasoning ?? '')}`;
  }
  if (event.type === 'run.completed') {
    return `run completed · ${String(event.payload.final_url ?? 'no final url')}`;
  }
  if (event.type === 'run.failed') {
    return `run failed · ${String(event.payload.error ?? 'unknown error')}`;
  }
  return JSON.stringify(event.payload);
}

export function createRunId() {
  if (typeof crypto !== 'undefined' && 'randomUUID' in crypto) {
    return `run-${crypto.randomUUID().slice(0, 12)}`;
  }
  return `run-${Math.random().toString(16).slice(2, 14)}`;
}

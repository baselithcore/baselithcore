import type { ReplayRun, ReplayRunSummary } from '../../lib/api';
import { truncate } from '../../lib/format';

export function statusTone(status: string): 'ok' | 'warn' | 'err' | 'muted' {
  switch (status) {
    case 'completed':
      return 'ok';
    case 'failed':
      return 'err';
    case 'running':
      return 'warn';
    default:
      return 'muted';
  }
}

export function formatDuration(startedAt: number, completedAt: number | null): string {
  const end = completedAt ?? Date.now() / 1000;
  const seconds = Math.max(0, end - startedAt);
  if (seconds < 5) return 'just started';
  if (seconds < 60) return `${seconds.toFixed(0)}s`;
  if (seconds < 3600) return `${(seconds / 60).toFixed(seconds >= 600 ? 0 : 1)}m`;
  return `${(seconds / 3600).toFixed(seconds >= 36_000 ? 0 : 1)}h`;
}

export function progressLabel(run: ReplayRunSummary): string {
  if (run.max_steps > 0) return `${run.step_count}/${run.max_steps} steps`;
  return `${run.step_count} steps`;
}

export function summarizedGoal(goal: string): string {
  return truncate(goal, 160);
}

export function lastKnownUrl(run: ReplayRun): string {
  const lastStep = run.steps[run.steps.length - 1];
  return run.final_url || lastStep?.current_url || run.start_url || '';
}

export function runDurationLabel(run: ReplayRunSummary | ReplayRun): string {
  return formatDuration(run.started_at, run.completed_at);
}

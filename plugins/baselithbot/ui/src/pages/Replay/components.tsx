import type { ReplayRun, ReplayRunSummary } from '../../lib/api';
import { formatRelative, truncate } from '../../lib/format';
import { formatDuration, progressLabel, statusTone, summarizedGoal } from './helpers';

export function RunCatalog({
  runs,
  selectedId,
  onSelect,
}: {
  runs: ReplayRunSummary[];
  selectedId: string | null;
  onSelect: (id: string) => void;
}) {
  return (
    <div className="replay-run-list">
      {runs.map((run) => {
        const active = run.run_id === selectedId;
        return (
          <button
            key={run.run_id}
            type="button"
            className={`replay-run-card ${active ? 'active' : ''}`}
            onClick={() => onSelect(run.run_id)}
          >
            <div className="replay-run-head">
              <span className="mono replay-run-id">{run.run_id}</span>
              <span className={`badge ${statusTone(run.status)}`}>{run.status}</span>
            </div>

            <div className="replay-run-goal">{summarizedGoal(run.goal)}</div>

            <div className="replay-run-meta">
              <span>{formatRelative(run.started_at)}</span>
              <span>{progressLabel(run)}</span>
              <span>{formatDuration(run.started_at, run.completed_at)}</span>
            </div>

            <div className="replay-run-chips">
              {run.start_url ? <span className="badge muted">start URL</span> : null}
              {run.final_url ? <span className="badge ok">final URL</span> : null}
              {run.error ? <span className="badge err">error</span> : null}
            </div>
          </button>
        );
      })}
    </div>
  );
}

export function StepStrip({
  run,
  selectedStep,
  onSelect,
}: {
  run: ReplayRun;
  selectedStep: number;
  onSelect: (index: number) => void;
}) {
  return (
    <div className="replay-step-strip">
      {run.steps.map((step, index) => {
        const active = index === selectedStep;
        return (
          <button
            key={`${run.run_id}-${step.step_index}`}
            type="button"
            className={`replay-step-pill ${active ? 'active' : ''}`}
            onClick={() => onSelect(index)}
          >
            <span className="mono">#{step.step_index}</span>
            <span>{truncate(step.action || 'unknown', 24)}</span>
          </button>
        );
      })}
    </div>
  );
}

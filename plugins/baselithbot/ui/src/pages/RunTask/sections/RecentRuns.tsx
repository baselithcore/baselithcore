import { EmptyState } from '../../../components/EmptyState';
import { Panel } from '../../../components/Panel';
import type { RunTaskState } from '../../../lib/api';
import { formatRelative, truncate } from '../../../lib/format';
import { badgeTone } from '../helpers';

interface RecentRunsProps {
  runs: RunTaskState[];
  selectedRunId: string | null;
  setSelectedRunId: (id: string) => void;
}

export function RecentRuns({ runs, selectedRunId, setSelectedRunId }: RecentRunsProps) {
  return (
    <Panel title="Recent runs" tag={`${runs.length}`}>
      {runs.length === 0 ? (
        <EmptyState
          title="No runs yet"
          description="Once tasks are dispatched, recent runs will appear here for quick reinspection."
        />
      ) : (
        <div className="stack-list">
          {runs.map((run) => (
            <button
              key={run.run_id}
              type="button"
              className={`select-row ${run.run_id === selectedRunId ? 'active' : ''}`}
              onClick={() => setSelectedRunId(run.run_id)}
            >
              <div className="select-row-head">
                <span className="badge muted mono">{run.run_id}</span>
                <span className={`badge ${badgeTone(run.status)}`}>{run.status}</span>
              </div>
              <div style={{ color: 'var(--ink-100)', fontSize: 13 }}>{truncate(run.goal, 110)}</div>
              <div className="muted" style={{ fontSize: 12 }}>
                {run.steps_taken}/{run.max_steps} steps · {formatRelative(run.started_at)}
              </div>
            </button>
          ))}
        </div>
      )}
    </Panel>
  );
}

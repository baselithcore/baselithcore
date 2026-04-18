import { EmptyState } from '../../../components/EmptyState';
import { Panel } from '../../../components/Panel';
import type { RunTaskState } from '../../../lib/api';
import { formatAbsolute } from '../../../lib/format';
import { MetaTile } from '../components';
import { badgeTone } from '../helpers';

interface LiveRunProps {
  selectedRun: RunTaskState | null;
  errorMsg: string | null;
  isPending: boolean;
  stepRatio: number;
}

export function LiveRun({ selectedRun, errorMsg, isPending, stepRatio }: LiveRunProps) {
  return (
    <Panel
      title="Live run"
      tag={selectedRun ? `${selectedRun.steps_taken}/${selectedRun.max_steps} steps` : ''}
    >
      {errorMsg && !selectedRun && (
        <div className="empty" style={{ color: 'var(--accent-rose)' }}>
          <strong>Dispatch failed</strong>
          <div className="muted">{errorMsg}</div>
        </div>
      )}

      {!selectedRun && !errorMsg && isPending && (
        <div className="empty">
          <strong>Dispatching run</strong>
          <div className="muted">
            Tracker state will appear here as soon as the backend accepts the task.
          </div>
        </div>
      )}

      {!selectedRun && !errorMsg && !isPending && (
        <EmptyState
          title="No run selected"
          description="Launch a task or pick one from the recent runs list to inspect its live state."
        />
      )}

      {selectedRun && (
        <div className="run-layout">
          <div className="run-summary">
            <div className="inline">
              <span className={`badge ${badgeTone(selectedRun.status)}`}>
                {selectedRun.status}
              </span>
              <span className="badge">step {selectedRun.steps_taken}</span>
              <span className="badge muted mono">{selectedRun.run_id}</span>
            </div>

            <div className="info-block">
              <strong style={{ display: 'block', marginBottom: 6, color: 'var(--ink-100)' }}>
                Goal
              </strong>
              {selectedRun.goal}
            </div>

            <div className="progress-track">
              <div className="progress-bar" style={{ width: `${stepRatio}%` }} />
            </div>

            <div className="detail-grid">
              <MetaTile label="Started" value={formatAbsolute(selectedRun.started_at)} />
              <MetaTile
                label="Completed"
                value={selectedRun.completed_at ? formatAbsolute(selectedRun.completed_at) : '—'}
              />
              <MetaTile
                label="Current URL"
                value={selectedRun.current_url || selectedRun.final_url || '—'}
              />
              <MetaTile label="Last action" value={selectedRun.last_action || 'waiting'} />
            </div>

            {selectedRun.last_reasoning && (
              <div className="info-block">
                <strong style={{ display: 'block', marginBottom: 6, color: 'var(--ink-100)' }}>
                  Latest reasoning
                </strong>
                {selectedRun.last_reasoning}
              </div>
            )}

            {selectedRun.error && <div className="run-error">{selectedRun.error}</div>}
          </div>

          <div className="run-preview">
            {selectedRun.last_screenshot_b64 ? (
              <img
                className="screenshot"
                alt="Latest task screenshot"
                src={`data:image/png;base64,${selectedRun.last_screenshot_b64}`}
              />
            ) : (
              <EmptyState
                title="No preview yet"
                description="The latest browser screenshot will appear here as soon as the run observes a page."
              />
            )}
          </div>
        </div>
      )}
    </Panel>
  );
}

import { Panel } from '../../../components/Panel';
import type { ReplayRun, ReplayRunsResponse } from '../../../lib/api';
import { formatAbsolute, formatNumber, formatRelative, truncate } from '../../../lib/format';
import { progressLabel, runDurationLabel, summarizedGoal } from '../helpers';

export function CommandPanel({
  data,
  runs,
  run,
  latestUrl,
  selectionStatusTone,
  runningCount,
  completedCount,
  failedCount,
}: {
  data: ReplayRunsResponse | undefined;
  runs: { length: number };
  run: ReplayRun | null;
  latestUrl: string;
  selectionStatusTone: 'ok' | 'warn' | 'err' | 'muted';
  runningCount: number;
  completedCount: number;
  failedCount: number;
}) {
  return (
    <Panel className="replay-command-panel">
      <div className="replay-command-grid">
        <div className="replay-command-copy">
          <div className="replay-command-head">
            <span className="badge muted">sqlite store</span>
            <span className="badge muted">
              retention {formatNumber(data?.retention_days ?? 14)} days
            </span>
            <span className={`badge ${runningCount > 0 ? 'warn' : 'muted'}`}>
              {runningCount > 0 ? `${formatNumber(runningCount)} running` : 'catalog idle'}
            </span>
          </div>

          <h2>Recorded execution timeline</h2>
          <p>
            Replay persists every run into SQLite so you can inspect screenshots, reasoning, URLs,
            and extracted payloads after execution. The selected run stays linked to live `run.*`
            updates while it is still in progress.
          </p>

          <div className="replay-mini-stats">
            <div className="replay-mini-stat">
              <span className="meta-label">Runs</span>
              <strong>{formatNumber(runs.length)}</strong>
              <span className="muted">visible catalog window</span>
            </div>
            <div className="replay-mini-stat">
              <span className="meta-label">Steps</span>
              <strong>{formatNumber(data?.step_totals ?? 0)}</strong>
              <span className="muted">persisted in current slice</span>
            </div>
            <div className="replay-mini-stat">
              <span className="meta-label">Latest Start</span>
              <strong>
                {data?.latest_started_ts ? formatRelative(data.latest_started_ts) : '—'}
              </strong>
              <span className="muted">
                {data?.latest_started_ts
                  ? formatAbsolute(data.latest_started_ts)
                  : 'No run in catalog'}
              </span>
            </div>
            <div className="replay-mini-stat">
              <span className="meta-label">Latest Completion</span>
              <strong>
                {data?.latest_completed_ts ? formatRelative(data.latest_completed_ts) : '—'}
              </strong>
              <span className="muted">
                {data?.latest_completed_ts
                  ? formatAbsolute(data.latest_completed_ts)
                  : 'No finished run yet'}
              </span>
            </div>
          </div>

          <div className="replay-command-footer">
            <div className="replay-db-tile">
              <span className="meta-label">Replay DB</span>
              <strong>{truncate(data?.path ?? 'replay.sqlite', 48)}</strong>
              <span className="mono replay-path-preview">{data?.path}</span>
            </div>

            <div className="replay-status-row">
              <div className="replay-status-chip">
                <span>Running</span>
                <strong>{formatNumber(runningCount)}</strong>
              </div>
              <div className="replay-status-chip">
                <span>Completed</span>
                <strong>{formatNumber(completedCount)}</strong>
              </div>
              <div className="replay-status-chip">
                <span>Failed</span>
                <strong>{formatNumber(failedCount)}</strong>
              </div>
            </div>
          </div>
        </div>

        <div className="replay-selection-card">
          <div className="replay-selection-head">
            <span className="meta-label">Selected run</span>
            <span className={`badge ${selectionStatusTone}`}>
              {run ? run.status : 'no selection'}
            </span>
          </div>

          {run ? (
            <>
              <h3>{summarizedGoal(run.goal)}</h3>
              <div className="replay-selection-meta">
                <div className="replay-selection-kv">
                  <span>Run ID</span>
                  <span className="mono">{run.run_id}</span>
                </div>
                <div className="replay-selection-kv">
                  <span>Duration</span>
                  <span>{runDurationLabel(run)}</span>
                </div>
                <div className="replay-selection-kv">
                  <span>Progress</span>
                  <span>{progressLabel(run)}</span>
                </div>
                <div className="replay-selection-kv">
                  <span>Screenshots</span>
                  <span>{formatNumber(run.screenshot_steps)}</span>
                </div>
              </div>

              <div className="replay-selection-url">
                <span className="section-label">Last known URL</span>
                <div className="info-block mono replay-url-block">
                  {latestUrl || 'No page URL available'}
                </div>
              </div>
            </>
          ) : (
            <div className="replay-sidecallout">
              Select a run from the catalog to inspect its step timeline.
            </div>
          )}
        </div>
      </div>
    </Panel>
  );
}

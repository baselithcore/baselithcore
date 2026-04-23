import { Panel } from '../../../components/Panel';
import type { ReplayRun } from '../../../lib/api';
import { formatAbsolute, formatNumber, formatRelative, truncate } from '../../../lib/format';
import { formatDuration, progressLabel, statusTone } from '../helpers';

export function RunSummary({ run, latestUrl }: { run: ReplayRun; latestUrl: string }) {
  return (
    <Panel className="replay-run-summary-panel">
      <div className="replay-run-summary">
        <div className="replay-run-summary-copy">
          <div className="replay-run-summary-head">
            <span className={`badge ${statusTone(run.status)}`}>{run.status}</span>
            <span className="badge muted mono">{run.run_id}</span>
          </div>
          <h2>{run.goal}</h2>
          <p>
            {run.status === 'running'
              ? 'This run is still live. New steps, screenshots, and extracted data will continue to appear here.'
              : 'Persisted execution record for post-run debugging, audit, and extraction review.'}
          </p>

          <div className="chip-row">
            <span className="badge muted">{progressLabel(run)}</span>
            <span className="badge muted">
              {formatNumber(run.screenshot_steps)} screenshot steps
            </span>
            <span className="badge muted">
              {formatNumber(run.distinct_url_count)} distinct URLs
            </span>
            {run.error ? <span className="badge err">terminal error</span> : null}
          </div>
        </div>

        <div className="detail-grid">
          <div className="meta-tile">
            <span className="meta-label">Started</span>
            <span>{formatRelative(run.started_at)}</span>
            <span className="muted">{formatAbsolute(run.started_at)}</span>
          </div>
          <div className="meta-tile">
            <span className="meta-label">Completed</span>
            <span>{run.completed_at ? formatRelative(run.completed_at) : 'Still running'}</span>
            <span className="muted">
              {run.completed_at ? formatAbsolute(run.completed_at) : 'Awaiting terminal event'}
            </span>
          </div>
          <div className="meta-tile">
            <span className="meta-label">Duration</span>
            <span>{formatDuration(run.started_at, run.completed_at)}</span>
            <span className="muted">{progressLabel(run)}</span>
          </div>
          <div className="meta-tile">
            <span className="meta-label">Final URL</span>
            <span>{latestUrl ? truncate(latestUrl, 42) : '—'}</span>
            <span className="muted">{latestUrl || 'No page URL captured yet'}</span>
          </div>
        </div>

        {run.error ? (
          <div className="replay-error-callout">
            <span className="section-label">Failure reason</span>
            <div className="info-block">{run.error}</div>
          </div>
        ) : null}
      </div>
    </Panel>
  );
}

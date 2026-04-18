import { Panel } from '../../../components/Panel';
import type { ApprovalListResponse, ApprovalPolicy } from '../../../lib/api';
import { formatAbsolute, formatNumber, formatRelative } from '../../../lib/format';
import { countdownLabel, formatDuration } from '../helpers';

interface HeroPanelProps {
  data: ApprovalListResponse;
  policy: ApprovalPolicy;
  now: number;
  pendingCount: number;
  approvedCount: number;
  deniedCount: number;
  timedOutCount: number;
}

export function HeroPanel({
  data,
  policy,
  now,
  pendingCount,
  approvedCount,
  deniedCount,
  timedOutCount,
}: HeroPanelProps) {
  return (
    <Panel className="approvals-hero-panel">
      <div className="approvals-hero">
        <div className="approvals-hero-copy">
          <span className="badge muted">runtime gate</span>
          <h2>Operator approval pipeline</h2>
          <p>
            This queue is wired directly to `ComputerUseConfig.require_approval_for`. When one of
            those enabled capabilities is invoked, Baselithbot pauses the live request here until
            an operator approves, denies, or lets the timeout auto-deny it.
          </p>

          <div className="chip-row">
            <span className={`badge ${policy.enabled ? 'ok' : 'warn'}`}>
              {policy.enabled ? 'gate armed' : 'gate disabled'}
            </span>
            <span className={`badge ${pendingCount > 0 ? 'warn' : 'muted'}`}>
              {pendingCount > 0 ? `${formatNumber(pendingCount)} pending` : 'queue idle'}
            </span>
            <span className="badge muted">
              timeout {formatDuration(policy.approval_timeout_seconds)}
            </span>
            <span className={`badge ${policy.gated_capabilities.length > 0 ? 'ok' : 'muted'}`}>
              {formatNumber(policy.gated_capabilities.length)} gated surfaces
            </span>
          </div>

          <div className="approvals-hero-metrics">
            <div className="approvals-hero-metric">
              <span className="meta-label">Coverage</span>
              <strong>
                {formatNumber(policy.gated_capabilities.length)} /{' '}
                {formatNumber(policy.enabled_capabilities.length || 0)}
              </strong>
              <span className="muted">Enabled Computer Use capabilities behind approval</span>
            </div>
            <div className="approvals-hero-metric">
              <span className="meta-label">Next expiry</span>
              <strong>
                {data.next_expiry_ts ? countdownLabel(data.next_expiry_ts, now) : '—'}
              </strong>
              <span className="muted">
                {data.next_expiry_ts ? formatAbsolute(data.next_expiry_ts) : 'No live request'}
              </span>
            </div>
            <div className="approvals-hero-metric">
              <span className="meta-label">Latest resolution</span>
              <strong>
                {data.latest_resolved_ts ? formatRelative(data.latest_resolved_ts) : '—'}
              </strong>
              <span className="muted">
                {data.latest_resolved_ts
                  ? formatAbsolute(data.latest_resolved_ts)
                  : 'History is still empty'}
              </span>
            </div>
          </div>
        </div>

        <div className="approvals-sidecard">
          <div className="approvals-sidecard-head">
            <span className="meta-label">Visible snapshot</span>
            <span className="badge muted">
              {formatNumber(data.totals.history)} resolved kept in memory
            </span>
          </div>

          <div className="approvals-kv">
            <span>Pending</span>
            <span>{formatNumber(pendingCount)}</span>
          </div>
          <div className="approvals-kv">
            <span>Approved</span>
            <span>{formatNumber(approvedCount)}</span>
          </div>
          <div className="approvals-kv">
            <span>Denied</span>
            <span>{formatNumber(deniedCount)}</span>
          </div>
          <div className="approvals-kv">
            <span>Timed out</span>
            <span>{formatNumber(timedOutCount)}</span>
          </div>

          <div className="approvals-sidecallout">
            Requests live in memory only. Approvals history here is a recent operator window,
            while the Audit Log remains the durable trail for the actual privileged action result.
          </div>
        </div>
      </div>
    </Panel>
  );
}

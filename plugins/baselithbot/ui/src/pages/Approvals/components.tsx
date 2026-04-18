import type { ApprovalRequest } from '../../lib/api';
import { formatAbsolute, formatRelative } from '../../lib/format';
import { Icon, paths } from '../../lib/icons';
import {
  countdownLabel,
  formatCapability,
  formatDuration,
  statusTone,
  summarizeParams,
  type DecisionInput,
} from './helpers';

export function PendingRequestCard({
  req,
  now,
  reason,
  busy,
  activeVerdict,
  onReasonChange,
  onApprove,
  onDeny,
  onSelect,
}: {
  req: ApprovalRequest;
  now: number;
  reason: string;
  busy: boolean;
  activeVerdict: DecisionInput['verdict'] | null;
  onReasonChange: (value: string) => void;
  onApprove: () => void;
  onDeny: () => void;
  onSelect: () => void;
}) {
  return (
    <article className="approval-request-card">
      <div className="approval-request-head">
        <div className="approval-request-title">
          <div className="approval-request-labels">
            <span className="badge warn">{formatCapability(req.capability)}</span>
            <span className="badge muted">{req.status}</span>
          </div>
          <h3>{req.action}</h3>
          <p>{summarizeParams(req.params)}</p>
        </div>

        <div className="approval-request-side">
          <span
            className={`badge ${
              Math.max(0, req.expires_at - now) <= 10
                ? 'err'
                : Math.max(0, req.expires_at - now) <= 30
                  ? 'warn'
                  : 'muted'
            }`}
          >
            {countdownLabel(req.expires_at, now)}
          </span>
          <button type="button" className="btn ghost sm" onClick={onSelect}>
            Details
          </button>
        </div>
      </div>

      <div className="approval-request-meta">
        <div className="meta-tile">
          <span className="meta-label">Submitted</span>
          <span>{formatRelative(req.submitted_at)}</span>
          <span className="muted">{formatAbsolute(req.submitted_at)}</span>
        </div>
        <div className="meta-tile">
          <span className="meta-label">Timeout</span>
          <span>{formatDuration(req.timeout_seconds)}</span>
          <span className="muted">Auto-deny when countdown reaches zero</span>
        </div>
        <div className="meta-tile">
          <span className="meta-label">Request ID</span>
          <span className="mono approval-request-id">{req.id}</span>
          <span className="muted">In-memory gate token</span>
        </div>
      </div>

      <label className="form-row">
        <span>Operator note</span>
        <textarea
          className="textarea approval-request-note"
          value={reason}
          placeholder="Why this request should be approved or denied"
          onChange={(event) => onReasonChange(event.target.value)}
          disabled={busy}
        />
      </label>

      <div className="approval-request-actions">
        <button type="button" className="btn primary" disabled={busy} onClick={onApprove}>
          <Icon path={paths.check} size={14} />
          {activeVerdict === 'approve' ? 'Approving…' : 'Approve request'}
        </button>
        <button type="button" className="btn danger" disabled={busy} onClick={onDeny}>
          <Icon path={paths.x} size={14} />
          {activeVerdict === 'deny' ? 'Denying…' : 'Deny request'}
        </button>
      </div>
    </article>
  );
}

export function HistoryRow({ req, onSelect }: { req: ApprovalRequest; onSelect: () => void }) {
  return (
    <button type="button" className="select-row approval-history-row" onClick={onSelect}>
      <div className="select-row-head">
        <div className="approval-history-title">
          <strong>{req.action}</strong>
          <span className="badge muted">{formatCapability(req.capability)}</span>
        </div>
        <span className={`badge ${statusTone(req.status)}`}>{req.status}</span>
      </div>

      <div className="approval-history-copy">{summarizeParams(req.params)}</div>

      <div className="approval-history-meta">
        <span>{req.resolved_at ? formatRelative(req.resolved_at) : 'pending'}</span>
        <span>{req.reason || 'No operator note'}</span>
      </div>
    </button>
  );
}

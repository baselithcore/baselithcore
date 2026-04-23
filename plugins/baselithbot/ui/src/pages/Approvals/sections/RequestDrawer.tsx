import { DetailDrawer } from '../../../components/DetailDrawer';
import type { ApprovalRequest } from '../../../lib/api';
import { formatAbsolute, formatRelative } from '../../../lib/format';
import {
  countdownLabel,
  formatCapability,
  formatDuration,
  statusTone,
  summarizeParams,
} from '../helpers';

interface RequestDrawerProps {
  selectedRequest: ApprovalRequest | null;
  now: number;
  onClose: () => void;
}

export function RequestDrawer({ selectedRequest, now, onClose }: RequestDrawerProps) {
  return (
    <DetailDrawer
      open={Boolean(selectedRequest)}
      title={selectedRequest?.action ?? 'Approval details'}
      subtitle={
        selectedRequest
          ? `${formatCapability(selectedRequest.capability)} · ${selectedRequest.status}`
          : undefined
      }
      onClose={onClose}
    >
      {selectedRequest ? (
        <div className="approvals-drawer-body">
          <div className="detail-grid">
            <div className="meta-tile">
              <span className="meta-label">Status</span>
              <span className={`badge ${statusTone(selectedRequest.status)}`}>
                {selectedRequest.status}
              </span>
            </div>
            <div className="meta-tile">
              <span className="meta-label">Submitted</span>
              <span>{formatRelative(selectedRequest.submitted_at)}</span>
              <span className="muted">{formatAbsolute(selectedRequest.submitted_at)}</span>
            </div>
            <div className="meta-tile">
              <span className="meta-label">Expires</span>
              <span>{formatAbsolute(selectedRequest.expires_at)}</span>
              <span className="muted">
                {selectedRequest.status === 'pending'
                  ? countdownLabel(selectedRequest.expires_at, now)
                  : `Timeout ${formatDuration(selectedRequest.timeout_seconds)}`}
              </span>
            </div>
            <div className="meta-tile">
              <span className="meta-label">Resolved</span>
              <span>
                {selectedRequest.resolved_at
                  ? formatRelative(selectedRequest.resolved_at)
                  : 'Still pending'}
              </span>
              <span className="muted">
                {selectedRequest.resolved_at
                  ? formatAbsolute(selectedRequest.resolved_at)
                  : 'Waiting for operator decision'}
              </span>
            </div>
          </div>

          <div className="approvals-drawer-sections">
            <div>
              <span className="section-label">Request summary</span>
              <div className="info-block">{summarizeParams(selectedRequest.params)}</div>
            </div>

            <div>
              <span className="section-label">Operator note</span>
              <div className="info-block">{selectedRequest.reason || 'No operator note'}</div>
            </div>

            <div>
              <span className="section-label">Params payload</span>
              <pre className="code-block">{JSON.stringify(selectedRequest.params, null, 2)}</pre>
            </div>
          </div>
        </div>
      ) : null}
    </DetailDrawer>
  );
}

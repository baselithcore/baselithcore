import { useEffect, useMemo, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { DetailDrawer } from '../components/DetailDrawer';
import { EmptyState } from '../components/EmptyState';
import { PageHeader } from '../components/PageHeader';
import { Panel } from '../components/Panel';
import { Skeleton } from '../components/Skeleton';
import { StatCard } from '../components/StatCard';
import { useToasts } from '../components/ToastProvider';
import { api, type ApprovalRequest, type ApprovalStatus } from '../lib/api';
import { formatAbsolute, formatNumber, formatRelative, truncate } from '../lib/format';
import { Icon, paths } from '../lib/icons';

type DecisionInput = {
  id: string;
  verdict: 'approve' | 'deny';
  reason: string;
};

function statusTone(status: ApprovalStatus): 'ok' | 'warn' | 'err' | 'muted' {
  switch (status) {
    case 'approved':
      return 'ok';
    case 'denied':
      return 'err';
    case 'timed_out':
      return 'warn';
    default:
      return 'muted';
  }
}

function formatCapability(capability: string): string {
  return capability.replace(/_/g, ' ');
}

function formatDuration(seconds: number | null | undefined): string {
  if (!seconds || !Number.isFinite(seconds) || seconds <= 0) return '—';
  if (seconds < 60) return `${seconds.toFixed(0)}s`;
  if (seconds < 3600) return `${(seconds / 60).toFixed(seconds >= 600 ? 0 : 1)}m`;
  return `${(seconds / 3600).toFixed(seconds >= 36_000 ? 0 : 1)}h`;
}

function summarizeParams(params: Record<string, unknown>): string {
  if (Array.isArray(params.argv) && params.argv.length > 0) {
    return params.argv.map((value) => String(value)).join(' ');
  }
  if (typeof params.path === 'string' && params.path) return params.path;
  if (typeof params.text === 'string' && params.text) return truncate(params.text, 120);

  const entries = Object.entries(params);
  if (entries.length === 0) return 'No params payload';

  return entries
    .slice(0, 3)
    .map(([key, value]) => {
      if (typeof value === 'string') return `${key}=${truncate(value, 36)}`;
      return `${key}=${truncate(JSON.stringify(value), 36)}`;
    })
    .join(' · ');
}

function countdownLabel(expiresAt: number, now: number): string {
  const remaining = Math.max(0, expiresAt - now);
  return remaining <= 0 ? 'expired' : `${remaining.toFixed(0)}s left`;
}

function topEntries(source: Record<string, number>, limit = 5): Array<[string, number]> {
  return Object.entries(source)
    .sort((left, right) => right[1] - left[1] || left[0].localeCompare(right[0]))
    .slice(0, limit);
}

function PendingRequestCard({
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

function HistoryRow({ req, onSelect }: { req: ApprovalRequest; onSelect: () => void }) {
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

export function Approvals() {
  const qc = useQueryClient();
  const { push } = useToasts();

  const [selectedRequestId, setSelectedRequestId] = useState<string | null>(null);
  const [reasonDrafts, setReasonDrafts] = useState<Record<string, string>>({});
  const [now, setNow] = useState(() => Date.now() / 1000);

  useEffect(() => {
    const intervalId = window.setInterval(() => setNow(Date.now() / 1000), 500);
    return () => window.clearInterval(intervalId);
  }, []);

  const { data, isLoading, isFetching, refetch } = useQuery({
    queryKey: ['approvals'],
    queryFn: api.approvals,
    refetchInterval: 2_500,
  });

  const decisionMutation = useMutation({
    mutationFn: ({ id, verdict, reason }: DecisionInput) =>
      verdict === 'approve'
        ? api.approveRequest(id, reason || undefined)
        : api.denyRequest(id, reason || undefined),
    onSuccess: (_result, variables) => {
      setReasonDrafts((prev) => {
        const next = { ...prev };
        delete next[variables.id];
        return next;
      });
      push({
        tone: 'success',
        title: variables.verdict === 'approve' ? 'Request approved' : 'Request denied',
        description: variables.id,
      });
      qc.invalidateQueries({ queryKey: ['approvals'] });
    },
    onError: (error: unknown) =>
      push({
        tone: 'error',
        title: 'Decision failed',
        description: error instanceof Error ? error.message : String(error),
      }),
  });

  const pending = data?.pending ?? [];
  const history = useMemo(() => (data?.history ?? []).slice().reverse(), [data]);
  const allRequests = useMemo(() => [...pending, ...(data?.history ?? [])], [data, pending]);
  const selectedRequest = allRequests.find((request) => request.id === selectedRequestId) ?? null;

  useEffect(() => {
    if (!selectedRequestId) return;
    if (!allRequests.some((request) => request.id === selectedRequestId)) {
      setSelectedRequestId(null);
    }
  }, [allRequests, selectedRequestId]);

  const policy = data?.policy;
  const statusCounts = data?.status_counts ?? {};
  const pendingCount = pending.length;
  const approvedCount = statusCounts.approved ?? 0;
  const deniedCount = statusCounts.denied ?? 0;
  const timedOutCount = statusCounts.timed_out ?? 0;
  const topCapabilities = useMemo(
    () => topEntries(data?.capability_counts ?? {}, 5),
    [data?.capability_counts]
  );
  const topActions = useMemo(() => topEntries(data?.action_counts ?? {}, 5), [data?.action_counts]);
  const activeMutationId = decisionMutation.variables?.id ?? null;

  const mutationLabel =
    decisionMutation.variables?.verdict === 'deny' ? 'Denying request…' : 'Approving request…';

  if (isLoading || !data || !policy) {
    return (
      <div className="approvals-page">
        <PageHeader
          eyebrow="Human Gate"
          title="Approvals"
          description="Operator decisions for privileged Computer Use actions routed through Baselithbot's human-in-the-loop gate."
        />
        <Skeleton height={320} />
      </div>
    );
  }

  return (
    <div className="approvals-page">
      <PageHeader
        eyebrow="Human Gate"
        title="Approvals"
        description="Approval queue for privileged Computer Use requests. Decisions here unblock or reject live shell, filesystem, mouse, keyboard, and screenshot actions."
        actions={
          <div className="inline">
            <button
              type="button"
              className="btn ghost"
              disabled={isFetching}
              onClick={() => refetch()}
            >
              <Icon path={paths.refresh} size={14} />
              {isFetching ? 'Refreshing…' : 'Refresh'}
            </button>
          </div>
        }
      />

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

      <section className="grid grid-cols-4">
        <StatCard
          label="Pending Queue"
          value={formatNumber(pendingCount)}
          sub="live approvals waiting for operator input"
          iconPath={paths.heart}
          accent="amber"
        />
        <StatCard
          label="Approved"
          value={formatNumber(approvedCount)}
          sub="resolved successfully in the visible window"
          iconPath={paths.check}
          accent="teal"
        />
        <StatCard
          label="Denied"
          value={formatNumber(deniedCount)}
          sub="rejected by operator"
          iconPath={paths.shieldOff}
          accent="rose"
        />
        <StatCard
          label="Timed Out"
          value={formatNumber(timedOutCount)}
          sub="auto-denied after timeout"
          iconPath={paths.clock}
          accent="violet"
        />
      </section>

      <section className="grid grid-split-2-1">
        <Panel title="Gate coverage" tag="computer use policy">
          <div className="detail-grid">
            <div className="meta-tile">
              <span className="meta-label">Enabled surfaces</span>
              <span>{formatNumber(policy.enabled_capabilities.length)}</span>
              <span className="muted">
                {policy.enabled_capabilities.length > 0
                  ? policy.enabled_capabilities.map(formatCapability).join(', ')
                  : 'Computer Use is effectively off'}
              </span>
            </div>
            <div className="meta-tile">
              <span className="meta-label">Gated surfaces</span>
              <span>{formatNumber(policy.gated_capabilities.length)}</span>
              <span className="muted">
                {policy.gated_capabilities.length > 0
                  ? policy.gated_capabilities.map(formatCapability).join(', ')
                  : 'No enabled capability requires approval'}
              </span>
            </div>
            <div className="meta-tile">
              <span className="meta-label">Bypassed surfaces</span>
              <span>{formatNumber(policy.bypassed_capabilities.length)}</span>
              <span className="muted">
                {policy.bypassed_capabilities.length > 0
                  ? policy.bypassed_capabilities.map(formatCapability).join(', ')
                  : 'All enabled capabilities are gated'}
              </span>
            </div>
            <div className="meta-tile">
              <span className="meta-label">Timeout policy</span>
              <span>{formatDuration(policy.approval_timeout_seconds)}</span>
              <span className="muted">Applies to every live approval request</span>
            </div>
          </div>

          <div className="approvals-policy-grid">
            <div className="approvals-policy-card">
              <span className="section-label">Gated capabilities</span>
              {policy.gated_capabilities.length === 0 ? (
                <div className="info-block">
                  No enabled Computer Use capability currently routes through the operator gate.
                </div>
              ) : (
                <div className="chip-row">
                  {policy.gated_capabilities.map((capability) => (
                    <span key={capability} className="badge ok">
                      {formatCapability(capability)}
                    </span>
                  ))}
                </div>
              )}
            </div>

            <div className="approvals-policy-card">
              <span className="section-label">Direct execution surfaces</span>
              {policy.bypassed_capabilities.length === 0 ? (
                <div className="info-block">No enabled surface bypasses the operator gate.</div>
              ) : (
                <div className="chip-row">
                  {policy.bypassed_capabilities.map((capability) => (
                    <span key={capability} className="badge muted">
                      {formatCapability(capability)}
                    </span>
                  ))}
                </div>
              )}
            </div>
          </div>
        </Panel>

        <Panel title="Hot paths" tag="visible requests">
          <div className="approvals-summary-block">
            <span className="section-label">Capabilities</span>
            {topCapabilities.length === 0 ? (
              <div className="info-block">No visible request mix yet.</div>
            ) : (
              <div className="approvals-summary-list">
                {topCapabilities.map(([capability, count]) => (
                  <div key={capability} className="approvals-kv">
                    <span>{formatCapability(capability)}</span>
                    <span>{formatNumber(count)}</span>
                  </div>
                ))}
              </div>
            )}
          </div>

          <div className="approvals-summary-block">
            <span className="section-label">Actions</span>
            {topActions.length === 0 ? (
              <div className="info-block">No action summary available yet.</div>
            ) : (
              <div className="approvals-summary-list">
                {topActions.map(([action, count]) => (
                  <div key={action} className="approvals-kv">
                    <span className="mono">{action}</span>
                    <span>{formatNumber(count)}</span>
                  </div>
                ))}
              </div>
            )}
          </div>
        </Panel>
      </section>

      {!policy.enabled ? (
        <EmptyState
          title="Approval gate disabled"
          description="`ComputerUseConfig.require_approval_for` is empty for the currently enabled surfaces, so privileged actions will not queue here."
        />
      ) : pending.length === 0 ? (
        <EmptyState
          title="No pending approvals"
          description="The gate is armed, but no live Computer Use request is currently waiting for operator input."
        />
      ) : (
        <Panel title="Pending queue" tag={`${formatNumber(pending.length)} live requests`}>
          <div className="approval-request-list">
            {pending.map((req) => {
              const activeVerdict =
                activeMutationId === req.id ? (decisionMutation.variables?.verdict ?? null) : null;
              return (
                <PendingRequestCard
                  key={req.id}
                  req={req}
                  now={now}
                  reason={reasonDrafts[req.id] ?? ''}
                  busy={decisionMutation.isPending}
                  activeVerdict={activeVerdict}
                  onReasonChange={(value) =>
                    setReasonDrafts((prev) => ({ ...prev, [req.id]: value }))
                  }
                  onApprove={() =>
                    decisionMutation.mutate({
                      id: req.id,
                      verdict: 'approve',
                      reason: reasonDrafts[req.id] ?? '',
                    })
                  }
                  onDeny={() =>
                    decisionMutation.mutate({
                      id: req.id,
                      verdict: 'deny',
                      reason: reasonDrafts[req.id] ?? '',
                    })
                  }
                  onSelect={() => setSelectedRequestId(req.id)}
                />
              );
            })}
          </div>

          {decisionMutation.isPending ? (
            <div className="approvals-inline-note">{mutationLabel}</div>
          ) : null}
        </Panel>
      )}

      <Panel title="Recent decisions" tag={`${formatNumber(history.length)} rows`}>
        {history.length === 0 ? (
          <div className="info-block">No approval history captured yet.</div>
        ) : (
          <div className="stack-list">
            {history.map((req) => (
              <HistoryRow key={req.id} req={req} onSelect={() => setSelectedRequestId(req.id)} />
            ))}
          </div>
        )}
      </Panel>

      <DetailDrawer
        open={Boolean(selectedRequest)}
        title={selectedRequest?.action ?? 'Approval details'}
        subtitle={
          selectedRequest
            ? `${formatCapability(selectedRequest.capability)} · ${selectedRequest.status}`
            : undefined
        }
        onClose={() => setSelectedRequestId(null)}
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
    </div>
  );
}

import { useMemo, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { api, type ApprovalRequest, type ApprovalStatus } from '../lib/api';
import { PageHeader } from '../components/PageHeader';
import { Panel } from '../components/Panel';
import { Skeleton } from '../components/Skeleton';
import { EmptyState } from '../components/EmptyState';
import { useToasts } from '../components/ToastProvider';
import { formatRelative } from '../lib/format';

function statusBadge(status: ApprovalStatus): string {
  switch (status) {
    case 'approved':
      return 'bg-emerald-900/40 text-emerald-300';
    case 'denied':
      return 'bg-red-900/40 text-red-300';
    case 'timed_out':
      return 'bg-amber-900/40 text-amber-300';
    default:
      return 'bg-sky-900/40 text-sky-300';
  }
}

function CountdownPill({ expiresAt }: { expiresAt: number }) {
  const [now, setNow] = useState(() => Date.now() / 1000);
  useMemo(() => {
    const id = window.setInterval(() => setNow(Date.now() / 1000), 500);
    return () => window.clearInterval(id);
  }, []);
  const remaining = Math.max(0, expiresAt - now);
  const tone =
    remaining < 10
      ? 'bg-red-900/40 text-red-300'
      : remaining < 30
        ? 'bg-amber-900/40 text-amber-300'
        : 'bg-zinc-800 text-zinc-300';
  return (
    <span className={`rounded px-2 py-0.5 text-[10px] uppercase font-mono ${tone}`}>
      {remaining.toFixed(0)}s
    </span>
  );
}

function PendingRow({
  req,
  onApprove,
  onDeny,
  isBusy,
}: {
  req: ApprovalRequest;
  onApprove: (id: string, reason: string) => void;
  onDeny: (id: string, reason: string) => void;
  isBusy: boolean;
}) {
  const [reason, setReason] = useState('');
  return (
    <tr className="border-t border-zinc-800/60 align-top">
      <td className="px-3 py-3 font-mono text-[11px] text-zinc-400">
        {formatRelative(req.submitted_at)}
      </td>
      <td className="px-3 py-3 text-xs">
        <div className="font-mono text-zinc-200">{req.action}</div>
        <div className="text-[10px] uppercase text-zinc-500">{req.capability}</div>
      </td>
      <td className="px-3 py-3">
        <pre className="whitespace-pre-wrap break-words font-mono text-[11px] text-zinc-300">
          {JSON.stringify(req.params, null, 0)}
        </pre>
      </td>
      <td className="px-3 py-3">
        <CountdownPill expiresAt={req.expires_at} />
      </td>
      <td className="px-3 py-3">
        <div className="flex flex-col gap-2">
          <input
            type="text"
            placeholder="reason (optional)"
            className="rounded border border-zinc-800 bg-zinc-950 p-1.5 text-[11px]"
            value={reason}
            onChange={(e) => setReason(e.target.value)}
            disabled={isBusy}
          />
          <div className="flex gap-2">
            <button
              type="button"
              disabled={isBusy}
              onClick={() => onApprove(req.id, reason)}
              className="rounded bg-emerald-600 px-3 py-1.5 text-[11px] font-medium text-white disabled:opacity-50"
            >
              Approve
            </button>
            <button
              type="button"
              disabled={isBusy}
              onClick={() => onDeny(req.id, reason)}
              className="rounded bg-red-700 px-3 py-1.5 text-[11px] font-medium text-white disabled:opacity-50"
            >
              Deny
            </button>
          </div>
        </div>
      </td>
    </tr>
  );
}

export function Approvals() {
  const qc = useQueryClient();
  const { push } = useToasts();

  const { data, isLoading } = useQuery({
    queryKey: ['approvals'],
    queryFn: api.approvals,
    refetchInterval: 1000,
  });

  const approve = useMutation({
    mutationFn: ({ id, reason }: { id: string; reason: string }) =>
      api.approveRequest(id, reason || undefined),
    onSuccess: (res) => {
      push({ tone: 'success', title: 'Approved', description: res.id });
      qc.invalidateQueries({ queryKey: ['approvals'] });
    },
    onError: (err: unknown) =>
      push({
        tone: 'error',
        title: 'Approve failed',
        description: err instanceof Error ? err.message : String(err),
      }),
  });

  const deny = useMutation({
    mutationFn: ({ id, reason }: { id: string; reason: string }) =>
      api.denyRequest(id, reason || undefined),
    onSuccess: (res) => {
      push({ tone: 'success', title: 'Denied', description: res.id });
      qc.invalidateQueries({ queryKey: ['approvals'] });
    },
    onError: (err: unknown) =>
      push({
        tone: 'error',
        title: 'Deny failed',
        description: err instanceof Error ? err.message : String(err),
      }),
  });

  const busy = approve.isPending || deny.isPending;
  const pending = data?.pending ?? [];
  const history = data?.history ?? [];

  return (
    <div className="space-y-4">
      <PageHeader
        title="Approvals"
        description="Human-in-the-loop gate for privileged Computer Use actions flagged by ComputerUseConfig.require_approval_for."
      />

      {isLoading ? (
        <Skeleton height={200} />
      ) : pending.length === 0 ? (
        <EmptyState
          title="No pending approvals"
          description="Privileged actions will appear here when they need operator confirmation. Configure ComputerUseConfig.require_approval_for to route capabilities through this gate."
        />
      ) : (
        <Panel>
          <header className="px-4 pt-4">
            <h3 className="text-sm font-semibold">Pending ({pending.length})</h3>
          </header>
          <div className="overflow-x-auto">
            <table className="min-w-full text-xs">
              <thead className="bg-zinc-900/80">
                <tr className="text-left text-zinc-400">
                  <th className="px-3 py-2">Submitted</th>
                  <th className="px-3 py-2">Action</th>
                  <th className="px-3 py-2">Params</th>
                  <th className="px-3 py-2">Expires</th>
                  <th className="px-3 py-2">Decision</th>
                </tr>
              </thead>
              <tbody>
                {pending.map((req) => (
                  <PendingRow
                    key={req.id}
                    req={req}
                    isBusy={busy}
                    onApprove={(id, reason) => approve.mutate({ id, reason })}
                    onDeny={(id, reason) => deny.mutate({ id, reason })}
                  />
                ))}
              </tbody>
            </table>
          </div>
        </Panel>
      )}

      <Panel>
        <header className="px-4 pt-4">
          <h3 className="text-sm font-semibold">Recent history</h3>
          <p className="text-xs text-zinc-400">
            Last 50 resolved approvals (approved / denied / timed out).
          </p>
        </header>
        {history.length === 0 ? (
          <div className="px-4 pb-4 pt-2 text-xs text-zinc-500">No history yet.</div>
        ) : (
          <div className="overflow-x-auto">
            <table className="min-w-full text-xs">
              <thead className="bg-zinc-900/80">
                <tr className="text-left text-zinc-400">
                  <th className="px-3 py-2">Resolved</th>
                  <th className="px-3 py-2">Action</th>
                  <th className="px-3 py-2">Status</th>
                  <th className="px-3 py-2">Reason</th>
                </tr>
              </thead>
              <tbody>
                {history
                  .slice()
                  .reverse()
                  .map((req) => (
                    <tr key={req.id} className="border-t border-zinc-800/60 align-top">
                      <td className="px-3 py-2 font-mono text-[11px] text-zinc-400">
                        {req.resolved_at ? formatRelative(req.resolved_at) : '—'}
                      </td>
                      <td className="px-3 py-2 font-mono">
                        {req.action}
                        <span className="ml-2 text-[10px] uppercase text-zinc-500">
                          {req.capability}
                        </span>
                      </td>
                      <td className="px-3 py-2">
                        <span
                          className={`rounded px-2 py-0.5 text-[10px] uppercase ${statusBadge(
                            req.status
                          )}`}
                        >
                          {req.status}
                        </span>
                      </td>
                      <td className="px-3 py-2 text-zinc-300">{req.reason ?? '—'}</td>
                    </tr>
                  ))}
              </tbody>
            </table>
          </div>
        )}
      </Panel>
    </div>
  );
}

import { useMemo, useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { api, type AuditEntry } from '../lib/api';
import { PageHeader } from '../components/PageHeader';
import { Panel } from '../components/Panel';
import { EmptyState } from '../components/EmptyState';
import { Skeleton } from '../components/Skeleton';
import { formatRelative } from '../lib/format';

const TAIL_OPTIONS = [50, 100, 200, 500, 1000];

function statusBadge(status: string | undefined): string {
  switch ((status ?? '').toLowerCase()) {
    case 'success':
      return 'bg-emerald-900/40 text-emerald-300';
    case 'denied':
      return 'bg-amber-900/40 text-amber-300';
    case 'error':
      return 'bg-red-900/40 text-red-300';
    default:
      return 'bg-zinc-800 text-zinc-300';
  }
}

export function AuditLog() {
  const [limit, setLimit] = useState(200);
  const [actionFilter, setActionFilter] = useState('');

  const { data, isLoading, refetch, isFetching } = useQuery({
    queryKey: ['audit-log', limit, actionFilter],
    queryFn: () => api.auditLog(limit, actionFilter || undefined),
    refetchInterval: 5_000,
  });

  const entries = useMemo<AuditEntry[]>(
    () => (data?.entries ?? []).slice().reverse(),
    [data],
  );

  return (
    <div className="space-y-4">
      <PageHeader
        title="Audit log"
        description="JSON-Lines audit of every privileged Computer Use action."
      />

      <Panel>
        <div className="flex flex-wrap items-end gap-3 px-4 py-3">
          <label className="text-xs">
            <span className="block text-zinc-400">Tail (rows)</span>
            <select
              className="mt-1 rounded border border-zinc-800 bg-zinc-950 p-2 text-sm"
              value={limit}
              onChange={(e) => setLimit(Number(e.target.value))}
            >
              {TAIL_OPTIONS.map((n) => (
                <option key={n} value={n}>
                  {n}
                </option>
              ))}
            </select>
          </label>
          <label className="text-xs">
            <span className="block text-zinc-400">Filter action contains</span>
            <input
              type="text"
              className="mt-1 rounded border border-zinc-800 bg-zinc-950 p-2 text-sm"
              value={actionFilter}
              placeholder="shell_run / fs_write / mouse_…"
              onChange={(e) => setActionFilter(e.target.value)}
            />
          </label>
          <div className="ml-auto flex items-center gap-3">
            {data?.path ? (
              <code className="text-[10px] text-zinc-500">{data.path}</code>
            ) : null}
            <button
              type="button"
              onClick={() => refetch()}
              disabled={isFetching}
              className="rounded border border-zinc-700 px-3 py-2 text-xs"
            >
              {isFetching ? 'Refreshing…' : 'Refresh'}
            </button>
          </div>
        </div>
      </Panel>

      {isLoading ? (
        <Skeleton height={256} />
      ) : !data?.configured ? (
        <EmptyState
          title="Audit log not configured"
          description="Set computer_use.audit_log_path from the Computer Use page to enable persistent audit."
        />
      ) : entries.length === 0 ? (
        <EmptyState
          title="No entries yet"
          description="Audit log file is empty or no entries match the current filter."
        />
      ) : (
        <Panel>
          <div className="overflow-x-auto">
            <table className="min-w-full text-xs">
              <thead className="sticky top-0 bg-zinc-900/80 backdrop-blur">
                <tr className="text-left text-zinc-400">
                  <th className="px-3 py-2">Time</th>
                  <th className="px-3 py-2">Action</th>
                  <th className="px-3 py-2">Status</th>
                  <th className="px-3 py-2">Detail</th>
                </tr>
              </thead>
              <tbody>
                {entries.map((entry, idx) => {
                  const ts =
                    typeof entry.ts === 'number'
                      ? formatRelative(entry.ts)
                      : '—';
                  const { ts: _ts, action, status, raw, ...rest } = entry;
                  return (
                    <tr
                      key={`${ts}-${idx}`}
                      className="border-t border-zinc-800/60 align-top"
                    >
                      <td className="px-3 py-2 font-mono text-[11px] text-zinc-400">
                        {ts}
                      </td>
                      <td className="px-3 py-2 font-mono">
                        {action ?? raw ?? '—'}
                      </td>
                      <td className="px-3 py-2">
                        <span
                          className={`rounded px-2 py-0.5 text-[10px] uppercase ${statusBadge(
                            status,
                          )}`}
                        >
                          {status ?? '—'}
                        </span>
                      </td>
                      <td className="px-3 py-2">
                        <pre className="whitespace-pre-wrap break-words text-[11px] text-zinc-300">
                          {Object.keys(rest).length
                            ? JSON.stringify(rest, null, 0)
                            : ''}
                        </pre>
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        </Panel>
      )}
    </div>
  );
}

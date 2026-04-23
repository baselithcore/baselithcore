import { useMemo, useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { DetailDrawer } from '../components/DetailDrawer';
import { EmptyState } from '../components/EmptyState';
import { PageHeader } from '../components/PageHeader';
import { Panel } from '../components/Panel';
import { Skeleton } from '../components/Skeleton';
import { StatCard } from '../components/StatCard';
import { api, type AuditEntry } from '../lib/api';
import { formatAbsolute, formatNumber, formatRelative } from '../lib/format';
import { Icon, paths } from '../lib/icons';

const TAIL_OPTIONS = [50, 100, 200, 500, 1000];

function statusTone(status: string | undefined): 'ok' | 'warn' | 'err' | 'muted' {
  switch ((status ?? '').toLowerCase()) {
    case 'success':
      return 'ok';
    case 'denied':
      return 'warn';
    case 'error':
      return 'err';
    default:
      return 'muted';
  }
}

function renderPrimaryDetail(entry: AuditEntry): string {
  if (typeof entry.raw === 'string' && entry.raw) return entry.raw;
  if (Array.isArray(entry.argv) && entry.argv.length > 0) return entry.argv.join(' ');
  if (typeof entry.path === 'string' && entry.path) return entry.path;
  if (typeof entry.cwd === 'string' && entry.cwd) return entry.cwd;
  const rest = Object.entries(entry).filter(
    ([key]) => !['ts', 'action', 'status', 'raw'].includes(key)
  );
  if (rest.length === 0) return 'No extra payload';
  return rest
    .slice(0, 2)
    .map(([key, value]) => `${key}=${typeof value === 'string' ? value : JSON.stringify(value)}`)
    .join(' · ');
}

export function AuditLog() {
  const [limit, setLimit] = useState(200);
  const [actionFilter, setActionFilter] = useState('');
  const [selectedKey, setSelectedKey] = useState<string | null>(null);

  const { data, isLoading, refetch, isFetching } = useQuery({
    queryKey: ['audit-log', limit, actionFilter],
    queryFn: () => api.auditLog(limit, actionFilter || undefined),
    refetchInterval: 5_000,
  });

  const entries = useMemo(() => (data?.entries ?? []).slice().reverse(), [data]);
  const selectedEntry = useMemo(() => {
    if (!selectedKey) return null;
    return (
      entries.find((entry, index) => {
        const entryKey = `${entry.ts ?? 'na'}:${entry.action ?? 'unknown'}:${index}`;
        return entryKey === selectedKey;
      }) ?? null
    );
  }, [entries, selectedKey]);

  const actionCounts = data?.action_counts ?? {};
  const topActions = useMemo(
    () =>
      Object.entries(actionCounts)
        .sort((left, right) => right[1] - left[1] || left[0].localeCompare(right[0]))
        .slice(0, 6),
    [actionCounts]
  );

  const statusCounts = data?.status_counts ?? {};
  const successCount = statusCounts.success ?? 0;
  const deniedCount = statusCounts.denied ?? 0;
  const errorCount = statusCounts.error ?? 0;
  const uniqueActions = Object.keys(actionCounts).length;

  const configured = Boolean(data?.configured);
  const fileExists = Boolean(data?.file_exists);
  const newestTs = typeof data?.newest_ts === 'number' ? data.newest_ts : null;

  return (
    <div className="audit-page">
      <PageHeader
        eyebrow="Computer Use Ledger"
        title="Audit Log"
        description="Append-only JSONL trail of privileged desktop, shell, and filesystem actions executed through Baselithbot."
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

      <Panel className="audit-hero-panel">
        <div className="audit-hero">
          <div className="audit-hero-copy">
            <span className="badge muted">jsonl sink</span>
            <h2>Privileged action trail</h2>
            <p>
              This ledger is fed by the `AuditLogger` used across Computer Use tools. It records
              successful, denied, timed-out, and error-path operations for shell, filesystem,
              screenshot, keyboard, and mouse surfaces.
            </p>

            <div className="chip-row">
              <span className={`badge ${configured ? 'ok' : 'err'}`}>
                {configured ? 'sink configured' : 'sink missing'}
              </span>
              <span className={`badge ${fileExists ? 'ok' : 'warn'}`}>
                {fileExists ? 'file present' : 'file absent'}
              </span>
              <span className="badge muted">
                tail window {formatNumber(data?.tail_window ?? limit)}
              </span>
              <span className={`badge ${actionFilter.trim() ? 'warn' : 'muted'}`}>
                {actionFilter.trim() ? `filter: ${actionFilter.trim()}` : 'no action filter'}
              </span>
            </div>

            <div className="audit-hero-metrics">
              <div className="audit-hero-metric">
                <span className="meta-label">Audit path</span>
                <strong>{configured ? 'Configured' : 'Unset'}</strong>
                <span className="mono audit-path-preview">
                  {data?.path || 'No audit_log_path configured'}
                </span>
              </div>
              <div className="audit-hero-metric">
                <span className="meta-label">Newest event</span>
                <strong>{newestTs ? formatRelative(newestTs) : '—'}</strong>
                <span className="muted">
                  {newestTs ? formatAbsolute(newestTs) : 'No event in window'}
                </span>
              </div>
              <div className="audit-hero-metric">
                <span className="meta-label">Rows scanned</span>
                <strong>{formatNumber(data?.scanned_rows ?? 0)}</strong>
                <span className="muted">
                  {formatNumber(data?.returned ?? 0)} row(s) after filter
                </span>
              </div>
            </div>
          </div>

          <div className="audit-sidecard">
            <div className="audit-sidecard-head">
              <span className="meta-label">Current window</span>
              <span className="badge muted">{formatNumber(entries.length)} visible</span>
            </div>

            <div className="audit-kv">
              <span>Success</span>
              <span>{formatNumber(successCount)}</span>
            </div>
            <div className="audit-kv">
              <span>Denied</span>
              <span>{formatNumber(deniedCount)}</span>
            </div>
            <div className="audit-kv">
              <span>Error</span>
              <span>{formatNumber(errorCount)}</span>
            </div>
            <div className="audit-kv">
              <span>Unique actions</span>
              <span>{formatNumber(uniqueActions)}</span>
            </div>

            <div className="audit-sidecallout">
              Tailing is file-based, so this page reflects the current JSONL sink directly rather
              than an in-memory event buffer.
            </div>
          </div>
        </div>
      </Panel>

      <section className="grid grid-cols-4">
        <StatCard
          label="Visible Rows"
          value={formatNumber(entries.length)}
          sub="rows returned after filter"
          iconPath={paths.activity}
          accent="teal"
        />
        <StatCard
          label="Success"
          value={formatNumber(successCount)}
          sub="completed privileged actions"
          iconPath={paths.check}
          accent="cyan"
        />
        <StatCard
          label="Denied"
          value={formatNumber(deniedCount)}
          sub="blocked by policy"
          iconPath={paths.shieldOff}
          accent="amber"
        />
        <StatCard
          label="Errors"
          value={formatNumber(errorCount)}
          sub="runtime failures in window"
          iconPath={paths.x}
          accent="rose"
        />
      </section>

      <section className="grid grid-split-2-1">
        <Panel title="Query window" tag="tail + filter">
          <div className="audit-toolbar">
            <label className="form-row">
              <span>Tail rows</span>
              <select
                className="select"
                value={limit}
                onChange={(event) => setLimit(Number(event.target.value))}
              >
                {TAIL_OPTIONS.map((value) => (
                  <option key={value} value={value}>
                    {value}
                  </option>
                ))}
              </select>
            </label>

            <label className="form-row audit-toolbar-grow">
              <span>Action contains</span>
              <input
                type="text"
                className="input"
                value={actionFilter}
                placeholder="shell_run / fs_write / mouse_click"
                onChange={(event) => setActionFilter(event.target.value)}
              />
            </label>
          </div>

          <div className="detail-grid">
            <div className="meta-tile">
              <span className="meta-label">Configured path</span>
              <span className="mono">{data?.path || 'None'}</span>
            </div>
            <div className="meta-tile">
              <span className="meta-label">File state</span>
              <span>
                {configured ? (fileExists ? 'Ready' : 'Missing on disk') : 'Not configured'}
              </span>
            </div>
            <div className="meta-tile">
              <span className="meta-label">Tail window</span>
              <span>{formatNumber(data?.tail_window ?? limit)}</span>
            </div>
            <div className="meta-tile">
              <span className="meta-label">Returned rows</span>
              <span>{formatNumber(data?.returned ?? 0)}</span>
            </div>
          </div>
        </Panel>

        <Panel title="Top actions" tag={`${formatNumber(uniqueActions)} unique`}>
          {topActions.length === 0 ? (
            <div className="info-block">No action summary available for the current window.</div>
          ) : (
            <div className="audit-action-summary">
              {topActions.map(([action, count]) => (
                <div key={action} className="audit-action-summary-item">
                  <span className="mono">{action}</span>
                  <span>{formatNumber(count)}</span>
                </div>
              ))}
            </div>
          )}
        </Panel>
      </section>

      {isLoading ? (
        <Skeleton height={320} />
      ) : !configured ? (
        <EmptyState
          title="Audit sink not configured"
          description="Set `computer_use.audit_log_path` from the Computer Use tab to persist privileged actions to disk."
        />
      ) : !fileExists ? (
        <EmptyState
          title="Audit file not created yet"
          description="The path is configured, but the JSONL file does not exist yet. It will appear after the first persisted privileged action."
        />
      ) : entries.length === 0 ? (
        <EmptyState
          title="No matching entries"
          description="The current tail window is empty or your action filter excludes every row in the selected slice."
        />
      ) : (
        <Panel title="Recent entries" tag={`${formatNumber(entries.length)} rows`}>
          <div className="audit-entry-list">
            {entries.map((entry, index) => {
              const entryKey = `${entry.ts ?? 'na'}:${entry.action ?? 'unknown'}:${index}`;
              const timestamp = typeof entry.ts === 'number' ? entry.ts : null;
              return (
                <button
                  key={entryKey}
                  type="button"
                  className="audit-entry-card"
                  onClick={() => setSelectedKey(entryKey)}
                >
                  <div className="audit-entry-head">
                    <div className="audit-entry-title">
                      <span className="mono">{entry.action ?? entry.raw ?? 'raw entry'}</span>
                      <span className={`badge ${statusTone(entry.status)}`}>
                        {entry.status ?? 'unknown'}
                      </span>
                    </div>
                    <div className="audit-entry-time">
                      <span>{timestamp ? formatRelative(timestamp) : '—'}</span>
                      <span className="mono">
                        {timestamp ? formatAbsolute(timestamp) : 'No timestamp'}
                      </span>
                    </div>
                  </div>

                  <div className="audit-entry-body">{renderPrimaryDetail(entry)}</div>

                  <div className="audit-entry-meta">
                    {timestamp ? (
                      <span className="badge muted">ts {timestamp.toFixed(3)}</span>
                    ) : null}
                    {'return_code' in entry ? (
                      <span className="badge muted">rc {String(entry.return_code)}</span>
                    ) : null}
                    {'bytes' in entry ? (
                      <span className="badge muted">{String(entry.bytes)} bytes</span>
                    ) : null}
                    {'stdout_bytes' in entry ? (
                      <span className="badge muted">stdout {String(entry.stdout_bytes)}</span>
                    ) : null}
                    {'stderr_bytes' in entry ? (
                      <span className="badge muted">stderr {String(entry.stderr_bytes)}</span>
                    ) : null}
                  </div>
                </button>
              );
            })}
          </div>
        </Panel>
      )}

      <DetailDrawer
        open={Boolean(selectedEntry)}
        title={selectedEntry?.action ?? selectedEntry?.raw ?? 'Audit entry'}
        subtitle={selectedEntry?.ts ? formatAbsolute(selectedEntry.ts) : 'Raw JSONL entry'}
        onClose={() => setSelectedKey(null)}
      >
        {selectedEntry && (
          <>
            <div className="detail-grid">
              <div className="meta-tile">
                <span className="meta-label">Status</span>
                <span>{selectedEntry.status ?? 'unknown'}</span>
              </div>
              <div className="meta-tile">
                <span className="meta-label">Action</span>
                <span>{selectedEntry.action ?? 'raw'}</span>
              </div>
              <div className="meta-tile">
                <span className="meta-label">Relative time</span>
                <span>{selectedEntry.ts ? formatRelative(selectedEntry.ts) : '—'}</span>
              </div>
            </div>

            <div className="stack-section">
              <div className="section-label">Payload</div>
              <pre className="code-block">{JSON.stringify(selectedEntry, null, 2)}</pre>
            </div>
          </>
        )}
      </DetailDrawer>
    </div>
  );
}

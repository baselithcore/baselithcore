import { useDeferredValue, useMemo, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { api, type CronJob } from '../lib/api';
import { useConfirm } from '../components/ConfirmProvider';
import { DetailDrawer } from '../components/DetailDrawer';
import { EmptyState } from '../components/EmptyState';
import { PageHeader } from '../components/PageHeader';
import { Panel } from '../components/Panel';
import { Skeleton } from '../components/Skeleton';
import { useToasts } from '../components/ToastProvider';
import { Icon, paths } from '../lib/icons';
import { formatAbsolute, formatNumber } from '../lib/format';

type StatusFilter = 'all' | 'enabled' | 'paused' | 'error';
type SortKey = 'next' | 'runs' | 'name';

export function Crons() {
  const qc = useQueryClient();
  const confirm = useConfirm();
  const { push } = useToasts();
  const [search, setSearch] = useState('');
  const [status, setStatus] = useState<StatusFilter>('all');
  const [sort, setSort] = useState<SortKey>('next');
  const [selectedName, setSelectedName] = useState<string | null>(null);
  const deferredSearch = useDeferredValue(search);

  const { data, isLoading } = useQuery({
    queryKey: ['crons'],
    queryFn: api.crons,
    refetchInterval: 5_000,
  });

  const jobs = data?.jobs ?? [];
  const selected = useMemo(
    () => jobs.find((job) => job.name === selectedName) ?? null,
    [jobs, selectedName]
  );
  const enabledCount = useMemo(() => jobs.filter((job) => job.enabled).length, [jobs]);
  const errorCount = useMemo(() => jobs.filter((job) => !!job.last_error).length, [jobs]);

  const filtered = useMemo(() => {
    const needle = deferredSearch.trim().toLowerCase();
    return jobs
      .filter((job) => {
        if (status === 'enabled' && !job.enabled) return false;
        if (status === 'paused' && job.enabled) return false;
        if (status === 'error' && !job.last_error) return false;
        if (!needle) return true;
        return (
          job.name.toLowerCase().includes(needle) ||
          String(job.interval_seconds).includes(needle) ||
          (job.last_error || '').toLowerCase().includes(needle)
        );
      })
      .sort((left, right) => {
        if (sort === 'name') return left.name.localeCompare(right.name);
        if (sort === 'runs') return right.runs - left.runs || left.name.localeCompare(right.name);
        return left.next_run_at - right.next_run_at || left.name.localeCompare(right.name);
      });
  }, [deferredSearch, jobs, sort, status]);

  const remove = useMutation({
    mutationFn: (name: string) => api.removeCron(name),
    onSuccess: (_, name) => {
      setSelectedName((current) => (current === name ? null : current));
      qc.invalidateQueries({ queryKey: ['crons'] });
      push({
        tone: 'success',
        title: 'Cron removed',
        description: `${name} was removed from the scheduler.`,
      });
    },
    onError: (err: unknown) =>
      push({
        tone: 'error',
        title: 'Cron removal failed',
        description: err instanceof Error ? err.message : String(err),
      }),
  });

  if (isLoading || !data) return <Skeleton height={280} />;

  if (jobs.length === 0)
    return (
      <>
        <PageHeader
          eyebrow="Scheduler"
          title="Cron jobs"
          description={`Backend: ${data.backend}. Interval-based periodic jobs run inside the plugin.`}
        />
        <EmptyState
          title="No cron jobs scheduled"
          description="Register jobs via the CronScheduler to surface them here."
        />
      </>
    );

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 24 }}>
      <PageHeader
        eyebrow="Scheduler"
        title="Cron jobs"
        description={`Backend: ${data.backend} · ${formatNumber(jobs.length)} jobs · ${formatNumber(enabledCount)} enabled · ${formatNumber(errorCount)} with errors.`}
      />

      <Panel>
        <div className="toolbar">
          <input
            className="input toolbar-grow"
            placeholder="Filter jobs…"
            value={search}
            onChange={(event) => setSearch(event.target.value)}
          />
          <select
            className="select"
            value={status}
            onChange={(event) => setStatus(event.target.value as StatusFilter)}
          >
            <option value="all">all statuses</option>
            <option value="enabled">enabled</option>
            <option value="paused">paused</option>
            <option value="error">error only</option>
          </select>
          <select
            className="select"
            value={sort}
            onChange={(event) => setSort(event.target.value as SortKey)}
          >
            <option value="next">sort: next run</option>
            <option value="runs">sort: runs</option>
            <option value="name">sort: name</option>
          </select>
        </div>

        {filtered.length === 0 ? (
          <EmptyState
            title="No cron jobs match"
            description="Adjust the filters to inspect another scheduled job."
          />
        ) : (
          <div className="cards-grid">
            {filtered.map((job) => (
              <button
                key={job.name}
                type="button"
                className="record-card"
                onClick={() => setSelectedName(job.name)}
              >
                <div className="record-card-head">
                  <div className="record-card-title mono">{job.name}</div>
                  <span
                    className={`badge ${job.last_error ? 'err' : job.enabled ? 'ok' : 'muted'}`}
                  >
                    {job.last_error ? 'error' : job.enabled ? 'enabled' : 'paused'}
                  </span>
                </div>
                <div className="record-card-meta">
                  <div className="record-kv">
                    <span>Interval</span>
                    <span className="mono">{job.interval_seconds}s</span>
                  </div>
                  <div className="record-kv">
                    <span>Runs</span>
                    <span className="mono">{formatNumber(job.runs)}</span>
                  </div>
                  <div className="record-kv">
                    <span>Next run</span>
                    <span className="mono">{formatAbsolute(job.next_run_at)}</span>
                  </div>
                  <div className="record-kv">
                    <span>Status</span>
                    <span>
                      {job.last_error ? 'Needs attention' : job.enabled ? 'Active' : 'Paused'}
                    </span>
                  </div>
                </div>
              </button>
            ))}
          </div>
        )}
      </Panel>

      <DetailDrawer
        open={!!selected}
        title={selected?.name ?? ''}
        subtitle="Cron job details"
        onClose={() => setSelectedName(null)}
      >
        {selected && (
          <>
            <div className="detail-grid">
              <MetaTile label="Interval" value={`${selected.interval_seconds}s`} />
              <MetaTile label="Runs" value={formatNumber(selected.runs)} />
              <MetaTile label="Next run" value={formatAbsolute(selected.next_run_at)} />
              <MetaTile
                label="Status"
                value={selected.last_error ? 'error' : selected.enabled ? 'enabled' : 'paused'}
              />
            </div>

            <div className="stack-section">
              <div className="section-label">Operational summary</div>
              <div className="info-block">
                {selected.last_error
                  ? 'The scheduler is keeping this job registered, but the last execution surfaced an error and needs inspection.'
                  : selected.enabled
                    ? 'This cron job is enabled and queued for its next periodic execution.'
                    : 'This cron job is registered but currently paused.'}
              </div>
            </div>

            {selected.last_error && (
              <div className="stack-section">
                <div className="section-label">Last error</div>
                <pre className="code-block">{selected.last_error}</pre>
              </div>
            )}

            <button
              type="button"
              className="btn danger"
              disabled={remove.isPending}
              onClick={async () => {
                if (
                  !(await confirm({
                    title: 'Remove cron job',
                    description: `The job "${selected.name}" will be removed from the scheduler.`,
                    confirmLabel: 'Remove job',
                    tone: 'danger',
                  }))
                ) {
                  return;
                }
                remove.mutate(selected.name);
              }}
            >
              <Icon path={paths.trash} size={14} />
              Remove job
            </button>
          </>
        )}
      </DetailDrawer>
    </div>
  );
}

function MetaTile({ label, value }: { label: string; value: string }) {
  return (
    <div className="meta-tile">
      <span className="meta-label">{label}</span>
      <span>{value}</span>
    </div>
  );
}

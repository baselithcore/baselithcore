import { useDeferredValue, useEffect, useMemo, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { api, type CronActionCatalogEntry, type CronJob, type CustomCronPayload } from '../lib/api';
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
  const [intervalDraft, setIntervalDraft] = useState<string>('');
  const [formOpen, setFormOpen] = useState(false);
  const deferredSearch = useDeferredValue(search);

  const { data, isLoading } = useQuery({
    queryKey: ['crons'],
    queryFn: api.crons,
    refetchInterval: 5_000,
  });

  const { data: catalog } = useQuery({
    queryKey: ['crons', 'catalog'],
    queryFn: api.cronCatalog,
    staleTime: 60_000,
  });

  const jobs = data?.jobs ?? [];
  const selected = useMemo(
    () => jobs.find((job) => job.name === selectedName) ?? null,
    [jobs, selectedName]
  );
  const enabledCount = useMemo(() => jobs.filter((job) => job.enabled).length, [jobs]);
  const errorCount = useMemo(() => jobs.filter((job) => !!job.last_error).length, [jobs]);

  useEffect(() => {
    if (selected) setIntervalDraft(String(selected.interval_seconds));
  }, [selected?.name, selected?.interval_seconds]);

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
          (job.description || '').toLowerCase().includes(needle) ||
          (job.last_error || '').toLowerCase().includes(needle)
        );
      })
      .sort((left, right) => {
        if (sort === 'name') return left.name.localeCompare(right.name);
        if (sort === 'runs') return right.runs - left.runs || left.name.localeCompare(right.name);
        return left.next_run_at - right.next_run_at || left.name.localeCompare(right.name);
      });
  }, [deferredSearch, jobs, sort, status]);

  const invalidate = () => qc.invalidateQueries({ queryKey: ['crons'] });

  const remove = useMutation({
    mutationFn: (name: string) => api.removeCron(name),
    onSuccess: (_, name) => {
      setSelectedName((current) => (current === name ? null : current));
      invalidate();
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

  const toggle = useMutation({
    mutationFn: ({ name, enabled }: { name: string; enabled: boolean }) =>
      api.toggleCron(name, enabled),
    onSuccess: (_, { name, enabled }) => {
      invalidate();
      push({
        tone: 'success',
        title: enabled ? 'Cron resumed' : 'Cron paused',
        description: `${name} is now ${enabled ? 'enabled' : 'paused'}.`,
      });
    },
    onError: (err: unknown) =>
      push({
        tone: 'error',
        title: 'Cron toggle failed',
        description: err instanceof Error ? err.message : String(err),
      }),
  });

  const runNow = useMutation({
    mutationFn: (name: string) => api.runCron(name),
    onSuccess: (_, name) => {
      invalidate();
      push({
        tone: 'success',
        title: 'Cron triggered',
        description: `${name} will run on the next scheduler tick.`,
      });
    },
    onError: (err: unknown) =>
      push({
        tone: 'error',
        title: 'Cron trigger failed',
        description: err instanceof Error ? err.message : String(err),
      }),
  });

  const updateInterval = useMutation({
    mutationFn: ({ name, seconds }: { name: string; seconds: number }) =>
      api.updateCronInterval(name, seconds),
    onSuccess: (_, { name, seconds }) => {
      invalidate();
      push({
        tone: 'success',
        title: 'Cron interval updated',
        description: `${name} → every ${seconds}s.`,
      });
    },
    onError: (err: unknown) =>
      push({
        tone: 'error',
        title: 'Interval update failed',
        description: err instanceof Error ? err.message : String(err),
      }),
  });

  const create = useMutation({
    mutationFn: (payload: CustomCronPayload) => api.createCustomCron(payload),
    onSuccess: (_, payload) => {
      invalidate();
      setFormOpen(false);
      push({
        tone: 'success',
        title: 'Custom cron registered',
        description: `${payload.name} is now scheduled every ${payload.interval_seconds}s.`,
      });
    },
    onError: (err: unknown) =>
      push({
        tone: 'error',
        title: 'Custom cron create failed',
        description: err instanceof Error ? err.message : String(err),
      }),
  });

  const drawerBusy =
    toggle.isPending || runNow.isPending || updateInterval.isPending || remove.isPending;

  if (isLoading || !data) return <Skeleton height={280} />;

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
          <button type="button" className="btn" onClick={() => setFormOpen((open) => !open)}>
            {formOpen ? 'Close form' : 'New custom job'}
          </button>
        </div>

        {formOpen && catalog && (
          <CustomCronForm
            actions={catalog.actions}
            namePrefix={catalog.name_prefix}
            submitting={create.isPending}
            onSubmit={(payload) => create.mutate(payload)}
            onCancel={() => setFormOpen(false)}
          />
        )}

        {jobs.length === 0 ? (
          <EmptyState
            title="No cron jobs scheduled"
            description="Default maintenance jobs register at plugin startup. Use 'New custom job' to add your own."
          />
        ) : filtered.length === 0 ? (
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
                    <span>Kind</span>
                    <span>{job.custom ? 'Custom' : 'System'}</span>
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
        subtitle={selected?.custom ? 'Custom cron job' : 'Cron job details'}
        onClose={() => setSelectedName(null)}
      >
        {selected && (
          <>
            <div className="detail-grid">
              <MetaTile label="Interval" value={`${selected.interval_seconds}s`} />
              <MetaTile label="Runs" value={formatNumber(selected.runs)} />
              <MetaTile label="Next run" value={formatAbsolute(selected.next_run_at)} />
              <MetaTile
                label="Last run"
                value={selected.last_run_at ? formatAbsolute(selected.last_run_at) : '—'}
              />
              <MetaTile
                label="Status"
                value={selected.last_error ? 'error' : selected.enabled ? 'enabled' : 'paused'}
              />
              <MetaTile label="Kind" value={selected.custom ? 'custom' : 'system'} />
            </div>

            {selected.description && (
              <div className="stack-section">
                <div className="section-label">Description</div>
                <div className="info-block">{selected.description}</div>
              </div>
            )}

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

            <div className="stack-section">
              <div className="section-label">Controls</div>
              <div className="toolbar" style={{ flexWrap: 'wrap', gap: 8 }}>
                <button
                  type="button"
                  className="btn"
                  disabled={drawerBusy}
                  onClick={() => toggle.mutate({ name: selected.name, enabled: !selected.enabled })}
                >
                  {selected.enabled ? 'Pause' : 'Resume'}
                </button>
                <button
                  type="button"
                  className="btn"
                  disabled={drawerBusy || !selected.enabled}
                  onClick={() => runNow.mutate(selected.name)}
                >
                  Run now
                </button>
              </div>
            </div>

            <div className="stack-section">
              <div className="section-label">Interval (seconds)</div>
              <div className="toolbar" style={{ gap: 8 }}>
                <input
                  className="input"
                  type="number"
                  min={1}
                  max={86400}
                  step={1}
                  value={intervalDraft}
                  onChange={(event) => setIntervalDraft(event.target.value)}
                />
                <button
                  type="button"
                  className="btn"
                  disabled={
                    drawerBusy ||
                    !intervalDraft ||
                    Number.isNaN(Number(intervalDraft)) ||
                    Number(intervalDraft) === selected.interval_seconds ||
                    Number(intervalDraft) < 1
                  }
                  onClick={() =>
                    updateInterval.mutate({
                      name: selected.name,
                      seconds: Number(intervalDraft),
                    })
                  }
                >
                  Update interval
                </button>
              </div>
            </div>

            <button
              type="button"
              className="btn danger"
              disabled={drawerBusy}
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

interface CustomCronFormProps {
  actions: CronActionCatalogEntry[];
  namePrefix: string;
  submitting: boolean;
  onSubmit: (payload: CustomCronPayload) => void;
  onCancel: () => void;
}

function CustomCronForm({
  actions,
  namePrefix,
  submitting,
  onSubmit,
  onCancel,
}: CustomCronFormProps) {
  const [name, setName] = useState('');
  const [interval, setInterval] = useState('60');
  const [description, setDescription] = useState('');
  const [actionType, setActionType] = useState(actions[0]?.type ?? 'log');
  const [logMessage, setLogMessage] = useState('ping');
  const [logLevel, setLogLevel] = useState<'debug' | 'info' | 'warning'>('info');
  const [slashCommand, setSlashCommand] = useState('/status');
  const [webhookUrl, setWebhookUrl] = useState('https://');
  const [webhookBody, setWebhookBody] = useState('{}');
  const [webhookHeaders, setWebhookHeaders] = useState('{}');
  const [webhookTimeout, setWebhookTimeout] = useState('15');
  const [enabled, setEnabled] = useState(true);
  const [localError, setLocalError] = useState<string | null>(null);

  const handleSubmit = () => {
    setLocalError(null);
    const trimmedName = name.trim();
    if (!trimmedName) {
      setLocalError('Name is required.');
      return;
    }
    const secs = Number(interval);
    if (!Number.isFinite(secs) || secs < 1 || secs > 86400) {
      setLocalError('Interval must be between 1 and 86400 seconds.');
      return;
    }

    let params: Record<string, unknown>;
    try {
      if (actionType === 'log') {
        if (!logMessage.trim()) throw new Error('Message cannot be empty.');
        params = { message: logMessage, level: logLevel };
      } else if (actionType === 'chat_command') {
        if (!slashCommand.startsWith('/')) {
          throw new Error("Command must start with '/'.");
        }
        params = { command: slashCommand };
      } else if (actionType === 'http_webhook') {
        if (!webhookUrl.startsWith('http')) {
          throw new Error('URL must start with http:// or https://');
        }
        const body = webhookBody.trim() ? JSON.parse(webhookBody) : {};
        const headers = webhookHeaders.trim() ? JSON.parse(webhookHeaders) : {};
        const timeoutN = Number(webhookTimeout);
        if (!Number.isFinite(timeoutN) || timeoutN < 1 || timeoutN > 60) {
          throw new Error('Timeout must be between 1 and 60 seconds.');
        }
        params = {
          url: webhookUrl,
          body,
          headers,
          timeout_seconds: timeoutN,
        };
      } else {
        throw new Error(`Unsupported action '${actionType}'.`);
      }
    } catch (err) {
      setLocalError(err instanceof Error ? err.message : String(err));
      return;
    }

    onSubmit({
      name: trimmedName,
      interval_seconds: secs,
      action: { type: actionType, params },
      description: description.trim(),
      enabled,
    });
  };

  return (
    <div className="stack-section" style={{ marginTop: 12 }}>
      <div className="section-label">New custom cron job</div>
      <div style={{ display: 'grid', gap: 10 }}>
        <label className="field">
          <span>Name (prefix "{namePrefix}" auto-applied if missing)</span>
          <input
            className="input"
            placeholder="my-job"
            value={name}
            onChange={(event) => setName(event.target.value)}
          />
        </label>

        <label className="field">
          <span>Interval (seconds, 1–86400)</span>
          <input
            className="input"
            type="number"
            min={1}
            max={86400}
            step={1}
            value={interval}
            onChange={(event) => setInterval(event.target.value)}
          />
        </label>

        <label className="field">
          <span>Description (optional)</span>
          <input
            className="input"
            value={description}
            onChange={(event) => setDescription(event.target.value)}
          />
        </label>

        <label className="field">
          <span>Action</span>
          <select
            className="select"
            value={actionType}
            onChange={(event) => setActionType(event.target.value)}
          >
            {actions.map((entry) => (
              <option key={entry.type} value={entry.type}>
                {entry.label} — {entry.type}
              </option>
            ))}
          </select>
        </label>

        {actionType === 'log' && (
          <>
            <label className="field">
              <span>Message</span>
              <input
                className="input"
                value={logMessage}
                onChange={(event) => setLogMessage(event.target.value)}
              />
            </label>
            <label className="field">
              <span>Level</span>
              <select
                className="select"
                value={logLevel}
                onChange={(event) =>
                  setLogLevel(event.target.value as 'debug' | 'info' | 'warning')
                }
              >
                <option value="debug">debug</option>
                <option value="info">info</option>
                <option value="warning">warning</option>
              </select>
            </label>
          </>
        )}

        {actionType === 'chat_command' && (
          <label className="field">
            <span>Slash command (must start with /)</span>
            <input
              className="input"
              value={slashCommand}
              onChange={(event) => setSlashCommand(event.target.value)}
              placeholder="/status"
            />
          </label>
        )}

        {actionType === 'http_webhook' && (
          <>
            <label className="field">
              <span>URL</span>
              <input
                className="input"
                value={webhookUrl}
                onChange={(event) => setWebhookUrl(event.target.value)}
                placeholder="https://example.com/hook"
              />
            </label>
            <label className="field">
              <span>Body (JSON)</span>
              <textarea
                className="input"
                rows={3}
                value={webhookBody}
                onChange={(event) => setWebhookBody(event.target.value)}
              />
            </label>
            <label className="field">
              <span>Headers (JSON)</span>
              <textarea
                className="input"
                rows={2}
                value={webhookHeaders}
                onChange={(event) => setWebhookHeaders(event.target.value)}
              />
            </label>
            <label className="field">
              <span>Timeout (seconds, 1–60)</span>
              <input
                className="input"
                type="number"
                min={1}
                max={60}
                step={1}
                value={webhookTimeout}
                onChange={(event) => setWebhookTimeout(event.target.value)}
              />
            </label>
          </>
        )}

        <label className="field" style={{ flexDirection: 'row', gap: 8 }}>
          <input
            type="checkbox"
            checked={enabled}
            onChange={(event) => setEnabled(event.target.checked)}
          />
          <span>Enable immediately</span>
        </label>

        {localError && (
          <div className="info-block" style={{ color: 'var(--danger, crimson)' }}>
            {localError}
          </div>
        )}

        <div className="toolbar" style={{ gap: 8 }}>
          <button type="button" className="btn" disabled={submitting} onClick={handleSubmit}>
            {submitting ? 'Creating…' : 'Create cron job'}
          </button>
          <button type="button" className="btn" disabled={submitting} onClick={onCancel}>
            Cancel
          </button>
        </div>
      </div>
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

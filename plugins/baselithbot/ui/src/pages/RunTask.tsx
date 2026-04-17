import { useEffect, useMemo, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useSearchParams } from 'react-router-dom';
import { api, type RunTaskRequest, type RunTaskState } from '../lib/api';
import { useDashboardEvents } from '../lib/sse';
import { PageHeader } from '../components/PageHeader';
import { Panel } from '../components/Panel';
import { EmptyState } from '../components/EmptyState';
import { useToasts } from '../components/ToastProvider';
import { Icon, paths } from '../lib/icons';
import { formatAbsolute, formatRelative, truncate } from '../lib/format';

export function RunTask() {
  const queryClient = useQueryClient();
  const { push } = useToasts();
  const { events } = useDashboardEvents(400);
  const [searchParams, setSearchParams] = useSearchParams();

  const [goal, setGoal] = useState('');
  const [startUrl, setStartUrl] = useState('');
  const [maxSteps, setMaxSteps] = useState(20);
  const [extract, setExtract] = useState('');
  const [errorMsg, setErrorMsg] = useState<string | null>(null);
  const [selectedRunId, setSelectedRunId] = useState<string | null>(null);

  const latestRun = useQuery({
    queryKey: ['runTaskLatest'],
    queryFn: api.runTaskLatest,
    refetchInterval: 5_000,
  });

  const recentRuns = useQuery({
    queryKey: ['runTaskRecent', 8],
    queryFn: () => api.runTaskRecent(8),
    refetchInterval: 8_000,
  });

  const runDetail = useQuery({
    queryKey: ['runTaskById', selectedRunId],
    queryFn: () => api.runTaskById(selectedRunId!),
    enabled: !!selectedRunId,
    refetchInterval: (query) => (query.state.data?.run.status === 'running' ? 1_500 : 5_000),
  });

  useEffect(() => {
    const requestedRunId = searchParams.get('run');
    if (requestedRunId) {
      if (requestedRunId !== selectedRunId) {
        setSelectedRunId(requestedRunId);
      }
      return;
    }
    if (!selectedRunId && latestRun.data?.run?.run_id) {
      setSelectedRunId(latestRun.data.run.run_id);
    }
  }, [latestRun.data, searchParams, selectedRunId]);

  useEffect(() => {
    const currentRunId = searchParams.get('run');
    if ((currentRunId ?? null) === selectedRunId) return;
    const next = new URLSearchParams(searchParams);
    if (selectedRunId) next.set('run', selectedRunId);
    else next.delete('run');
    setSearchParams(next, { replace: true });
  }, [searchParams, selectedRunId, setSearchParams]);

  const selectedRun =
    runDetail.data?.run ??
    (latestRun.data?.run && latestRun.data.run.run_id === selectedRunId
      ? latestRun.data.run
      : null);

  const runEvents = useMemo(() => {
    if (!selectedRunId) return [];
    return events
      .filter((event) => {
        if (!event.type.startsWith('run.')) return false;
        const runId =
          event.payload && typeof event.payload === 'object' && 'run_id' in event.payload
            ? String(event.payload.run_id)
            : '';
        return runId === selectedRunId;
      })
      .slice()
      .reverse()
      .slice(0, 16);
  }, [events, selectedRunId]);

  const mutation = useMutation({
    mutationFn: (payload: RunTaskRequest) => api.runTask(payload),
    onMutate: (payload) => {
      setSelectedRunId(payload.run_id ?? null);
      setErrorMsg(null);
    },
    onSuccess: (data) => {
      const runId = data.run_id;
      if (runId) {
        setSelectedRunId(runId);
        queryClient.invalidateQueries({ queryKey: ['runTaskById', runId] });
      }
      push({
        tone: data.success ? 'success' : 'error',
        title: data.success ? 'Task completed' : 'Task finished with errors',
        description: data.error ?? `${data.steps_taken} steps executed.`,
      });
    },
    onError: (err: unknown, payload) => {
      const message = err instanceof Error ? err.message : String(err);
      setErrorMsg(message);
      if (payload.run_id) {
        queryClient.invalidateQueries({ queryKey: ['runTaskById', payload.run_id] });
      }
      push({
        tone: 'error',
        title: 'Task dispatch failed',
        description: message,
      });
    },
  });

  const disabled = mutation.isPending || goal.trim().length === 0;
  const stepRatio = selectedRun
    ? Math.min(100, (selectedRun.steps_taken / selectedRun.max_steps) * 100)
    : 0;

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 24 }}>
      <PageHeader
        eyebrow="Agent"
        title="Run an autonomous task"
        description="Dispatch a live Observe → Plan → Act run with progress tracking, current preview, execution timeline, and recent run history."
      />

      <section className="grid grid-split-1-2">
        <Panel title="Task" tag="POST /baselithbot/run">
          <form
            onSubmit={(e) => {
              e.preventDefault();
              if (disabled) return;
              mutation.mutate({
                run_id: createRunId(),
                goal: goal.trim(),
                start_url: startUrl.trim() || null,
                max_steps: maxSteps,
                extract_fields: extract
                  .split(',')
                  .map((s) => s.trim())
                  .filter(Boolean),
              });
            }}
            style={{ display: 'flex', flexDirection: 'column', gap: 14 }}
          >
            <div className="form-row">
              <label htmlFor="goal">Goal</label>
              <textarea
                id="goal"
                className="textarea"
                placeholder="e.g. Navigate to github.com and extract the trending repo titles"
                value={goal}
                onChange={(e) => setGoal(e.target.value)}
                required
                maxLength={4000}
              />
            </div>

            <div className="form-row">
              <label htmlFor="starturl">Start URL (optional)</label>
              <input
                id="starturl"
                className="input"
                type="url"
                placeholder="https://example.com"
                value={startUrl}
                onChange={(e) => setStartUrl(e.target.value)}
              />
            </div>

            <div className="inline">
              <div className="form-row" style={{ flex: '0 0 160px' }}>
                <label htmlFor="steps">Max steps</label>
                <input
                  id="steps"
                  className="input"
                  type="number"
                  min={1}
                  max={100}
                  value={maxSteps}
                  onChange={(e) =>
                    setMaxSteps(Math.max(1, Math.min(100, Number(e.target.value) || 20)))
                  }
                />
              </div>
              <div className="form-row" style={{ flex: 1 }}>
                <label htmlFor="extract">Extract fields (comma separated)</label>
                <input
                  id="extract"
                  className="input"
                  placeholder="title, price, rating"
                  value={extract}
                  onChange={(e) => setExtract(e.target.value)}
                />
              </div>
            </div>

            <div className="inline" style={{ justifyContent: 'flex-end' }}>
              <button
                type="button"
                className="btn ghost"
                disabled={mutation.isPending}
                onClick={() => {
                  setGoal('');
                  setStartUrl('');
                  setExtract('');
                  setMaxSteps(20);
                  setErrorMsg(null);
                }}
              >
                Reset
              </button>
              <button type="submit" className="btn primary" disabled={disabled}>
                {mutation.isPending ? (
                  <>
                    <span className="spin">
                      <Icon path={paths.refresh} size={14} />
                    </span>
                    Dispatching…
                  </>
                ) : (
                  <>
                    <Icon path={paths.play} size={14} />
                    Launch
                  </>
                )}
              </button>
            </div>
          </form>
        </Panel>

        <Panel
          title="Live run"
          tag={selectedRun ? `${selectedRun.steps_taken}/${selectedRun.max_steps} steps` : ''}
        >
          {errorMsg && !selectedRun && (
            <div className="empty" style={{ color: 'var(--accent-rose)' }}>
              <strong>Dispatch failed</strong>
              <div className="muted">{errorMsg}</div>
            </div>
          )}

          {!selectedRun && !errorMsg && mutation.isPending && (
            <div className="empty">
              <strong>Dispatching run</strong>
              <div className="muted">
                Tracker state will appear here as soon as the backend accepts the task.
              </div>
            </div>
          )}

          {!selectedRun && !errorMsg && !mutation.isPending && (
            <EmptyState
              title="No run selected"
              description="Launch a task or pick one from the recent runs list to inspect its live state."
            />
          )}

          {selectedRun && (
            <div className="run-layout">
              <div className="run-summary">
                <div className="inline">
                  <span className={`badge ${badgeTone(selectedRun.status)}`}>
                    {selectedRun.status}
                  </span>
                  <span className="badge">step {selectedRun.steps_taken}</span>
                  <span className="badge muted mono">{selectedRun.run_id}</span>
                </div>

                <div className="info-block">
                  <strong style={{ display: 'block', marginBottom: 6, color: 'var(--ink-100)' }}>
                    Goal
                  </strong>
                  {selectedRun.goal}
                </div>

                <div className="progress-track">
                  <div className="progress-bar" style={{ width: `${stepRatio}%` }} />
                </div>

                <div className="detail-grid">
                  <MetaTile label="Started" value={formatAbsolute(selectedRun.started_at)} />
                  <MetaTile
                    label="Completed"
                    value={
                      selectedRun.completed_at ? formatAbsolute(selectedRun.completed_at) : '—'
                    }
                  />
                  <MetaTile
                    label="Current URL"
                    value={selectedRun.current_url || selectedRun.final_url || '—'}
                  />
                  <MetaTile label="Last action" value={selectedRun.last_action || 'waiting'} />
                </div>

                {selectedRun.last_reasoning && (
                  <div className="info-block">
                    <strong style={{ display: 'block', marginBottom: 6, color: 'var(--ink-100)' }}>
                      Latest reasoning
                    </strong>
                    {selectedRun.last_reasoning}
                  </div>
                )}

                {selectedRun.error && <div className="run-error">{selectedRun.error}</div>}
              </div>

              <div className="run-preview">
                {selectedRun.last_screenshot_b64 ? (
                  <img
                    className="screenshot"
                    alt="Latest task screenshot"
                    src={`data:image/png;base64,${selectedRun.last_screenshot_b64}`}
                  />
                ) : (
                  <EmptyState
                    title="No preview yet"
                    description="The latest browser screenshot will appear here as soon as the run observes a page."
                  />
                )}
              </div>
            </div>
          )}
        </Panel>
      </section>

      <section className="grid grid-split-1-2">
        <Panel title="Recent runs" tag={`${recentRuns.data?.runs.length ?? 0}`}>
          {(recentRuns.data?.runs ?? []).length === 0 ? (
            <EmptyState
              title="No runs yet"
              description="Once tasks are dispatched, recent runs will appear here for quick reinspection."
            />
          ) : (
            <div className="stack-list">
              {(recentRuns.data?.runs ?? []).map((run) => (
                <button
                  key={run.run_id}
                  type="button"
                  className={`select-row ${run.run_id === selectedRunId ? 'active' : ''}`}
                  onClick={() => setSelectedRunId(run.run_id)}
                >
                  <div className="select-row-head">
                    <span className="badge muted mono">{run.run_id}</span>
                    <span className={`badge ${badgeTone(run.status)}`}>{run.status}</span>
                  </div>
                  <div style={{ color: 'var(--ink-100)', fontSize: 13 }}>
                    {truncate(run.goal, 110)}
                  </div>
                  <div className="muted" style={{ fontSize: 12 }}>
                    {run.steps_taken}/{run.max_steps} steps · {formatRelative(run.started_at)}
                  </div>
                </button>
              ))}
            </div>
          )}
        </Panel>

        <Panel title="Execution timeline" tag={selectedRun ? `${runEvents.length} events` : ''}>
          {!selectedRunId ? (
            <EmptyState
              title="No run selected"
              description="Select a run to inspect the live execution timeline emitted on the dashboard event bus."
            />
          ) : runEvents.length === 0 ? (
            <EmptyState
              title="Waiting for run events"
              description="Step-level run events will appear here as the task progresses."
            />
          ) : (
            <div className="trace">
              {runEvents.map((event, index) => (
                <div key={`${event.ts}-${index}`} className="step">
                  <span className="num">{event.type.replace('run.', '')}</span>
                  <div style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
                    <span className="text">{describeRunEvent(event)}</span>
                    <span className="muted mono" style={{ fontSize: 11 }}>
                      {formatRelative(event.ts)}
                    </span>
                  </div>
                </div>
              ))}
            </div>
          )}
        </Panel>
      </section>

      {selectedRun && (
        <section className="grid grid-split-2-1">
          <Panel title="History" tag={`${selectedRun.history.length}`}>
            {selectedRun.history.length === 0 ? (
              <EmptyState
                title="No history yet"
                description="The Observe → Plan → Act trace will appear here as soon as the run starts executing."
              />
            ) : (
              <div className="trace">
                {selectedRun.history.map((step, idx) => (
                  <div key={`${selectedRun.run_id}-${idx}`} className="step">
                    <span className="num">#{idx + 1}</span>
                    <span className="text">{step}</span>
                  </div>
                ))}
              </div>
            )}
          </Panel>

          <Panel
            title="Extracted data"
            tag={Object.keys(selectedRun.extracted_data).length.toString()}
          >
            {Object.keys(selectedRun.extracted_data).length === 0 ? (
              <EmptyState
                title="No extracted data"
                description="Any EXTRACT actions will accumulate their partial results here."
              />
            ) : (
              <ExtractedDataView data={selectedRun.extracted_data} />
            )}
          </Panel>
        </section>
      )}
    </div>
  );
}

function ExtractedDataView({ data }: { data: Record<string, unknown> }) {
  return (
    <div className="extracted-data">
      {Object.entries(data).map(([field, value]) => (
        <div key={field} className="extracted-field">
          <div className="extracted-field-header">
            <span className="extracted-field-name">{field}</span>
            <span className="extracted-field-count">
              {Array.isArray(value)
                ? `${value.length} item${value.length === 1 ? '' : 's'}`
                : typeof value}
            </span>
          </div>
          <ExtractedValue value={value} />
        </div>
      ))}
    </div>
  );
}

function ExtractedValue({ value }: { value: unknown }) {
  if (value === null || value === undefined) {
    return <span className="extracted-empty">—</span>;
  }
  if (Array.isArray(value)) {
    return (
      <ol className="extracted-list">
        {value.map((item, idx) => (
          <li key={idx}>
            <ExtractedValue value={item} />
          </li>
        ))}
      </ol>
    );
  }
  if (typeof value === 'object') {
    return (
      <dl className="extracted-object">
        {Object.entries(value as Record<string, unknown>).map(([k, v]) => (
          <div key={k} className="extracted-object-row">
            <dt>{k}</dt>
            <dd>
              <ExtractedValue value={v} />
            </dd>
          </div>
        ))}
      </dl>
    );
  }
  return <ExtractedScalar value={String(value)} />;
}

const URL_REGEX = /^https?:\/\/[^\s<>"]+$/i;
const EMAIL_REGEX = /^[^\s@]+@[^\s@]+\.[^\s@]+$/i;
const GITHUB_REPO_REGEX = /^([A-Za-z0-9][\w.-]*)\s*\/\s*([\w.-]+)$/;

function detectLink(raw: string): { href: string; label: string } | null {
  const trimmed = raw.trim();
  if (!trimmed) return null;
  if (URL_REGEX.test(trimmed)) {
    return { href: trimmed, label: trimmed };
  }
  if (EMAIL_REGEX.test(trimmed)) {
    return { href: `mailto:${trimmed}`, label: trimmed };
  }
  const repo = trimmed.match(GITHUB_REPO_REGEX);
  if (repo) {
    const [, org, name] = repo;
    return {
      href: `https://github.com/${org}/${name}`,
      label: `${org} / ${name}`,
    };
  }
  return null;
}

function ExtractedScalar({ value }: { value: string }) {
  const link = detectLink(value);
  if (!link) {
    return <span className="extracted-scalar">{value}</span>;
  }
  const isExternal = link.href.startsWith('http');
  return (
    <a
      className="extracted-link"
      href={link.href}
      target={isExternal ? '_blank' : undefined}
      rel={isExternal ? 'noopener noreferrer' : undefined}
      aria-label={isExternal ? `${link.label} (opens in new tab)` : link.label}
      title={link.href}
    >
      <span className="extracted-link-label">{link.label}</span>
      {isExternal ? (
        <Icon path={paths.externalLink} className="extracted-link-icon" size={12} aria-hidden />
      ) : null}
    </a>
  );
}

function MetaTile({ label, value }: { label: string; value: string }) {
  return (
    <div className="meta-tile">
      <span className="meta-label">{label}</span>
      <span className="mono" style={{ color: 'var(--ink-100)' }}>
        {value}
      </span>
    </div>
  );
}

function badgeTone(status: RunTaskState['status']) {
  if (status === 'completed') return 'ok';
  if (status === 'failed') return 'err';
  return 'warn';
}

function describeRunEvent(event: { type: string; payload: Record<string, unknown> }) {
  if (event.type === 'run.started') {
    return `run started · ${String(event.payload.goal ?? '')}`;
  }
  if (event.type === 'run.step') {
    return `${String(event.payload.action ?? 'step')} · ${String(event.payload.reasoning ?? '')}`;
  }
  if (event.type === 'run.completed') {
    return `run completed · ${String(event.payload.final_url ?? 'no final url')}`;
  }
  if (event.type === 'run.failed') {
    return `run failed · ${String(event.payload.error ?? 'unknown error')}`;
  }
  return JSON.stringify(event.payload);
}

function createRunId() {
  if (typeof crypto !== 'undefined' && 'randomUUID' in crypto) {
    return `run-${crypto.randomUUID().slice(0, 12)}`;
  }
  return `run-${Math.random().toString(16).slice(2, 14)}`;
}

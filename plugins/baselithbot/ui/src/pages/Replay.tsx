import { useEffect, useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { EmptyState } from '../components/EmptyState';
import { PageHeader } from '../components/PageHeader';
import { Panel } from '../components/Panel';
import { Skeleton } from '../components/Skeleton';
import { api, type ReplayRun, type ReplayRunSummary } from '../lib/api';
import { formatAbsolute, formatNumber, formatRelative, truncate } from '../lib/format';
import { Icon, paths } from '../lib/icons';

function statusTone(status: string): 'ok' | 'warn' | 'err' | 'muted' {
  switch (status) {
    case 'completed':
      return 'ok';
    case 'failed':
      return 'err';
    case 'running':
      return 'warn';
    default:
      return 'muted';
  }
}

function formatDuration(startedAt: number, completedAt: number | null): string {
  const end = completedAt ?? Date.now() / 1000;
  const seconds = Math.max(0, end - startedAt);
  if (seconds < 5) return 'just started';
  if (seconds < 60) return `${seconds.toFixed(0)}s`;
  if (seconds < 3600) return `${(seconds / 60).toFixed(seconds >= 600 ? 0 : 1)}m`;
  return `${(seconds / 3600).toFixed(seconds >= 36_000 ? 0 : 1)}h`;
}

function progressLabel(run: ReplayRunSummary): string {
  if (run.max_steps > 0) return `${run.step_count}/${run.max_steps} steps`;
  return `${run.step_count} steps`;
}

function summarizedGoal(goal: string): string {
  return truncate(goal, 160);
}

function lastKnownUrl(run: ReplayRun): string {
  const lastStep = run.steps[run.steps.length - 1];
  return run.final_url || lastStep?.current_url || run.start_url || '';
}

function runDurationLabel(run: ReplayRunSummary | ReplayRun): string {
  return formatDuration(run.started_at, run.completed_at);
}

function RunCatalog({
  runs,
  selectedId,
  onSelect,
}: {
  runs: ReplayRunSummary[];
  selectedId: string | null;
  onSelect: (id: string) => void;
}) {
  return (
    <div className="replay-run-list">
      {runs.map((run) => {
        const active = run.run_id === selectedId;
        return (
          <button
            key={run.run_id}
            type="button"
            className={`replay-run-card ${active ? 'active' : ''}`}
            onClick={() => onSelect(run.run_id)}
          >
            <div className="replay-run-head">
              <span className="mono replay-run-id">{run.run_id}</span>
              <span className={`badge ${statusTone(run.status)}`}>{run.status}</span>
            </div>

            <div className="replay-run-goal">{summarizedGoal(run.goal)}</div>

            <div className="replay-run-meta">
              <span>{formatRelative(run.started_at)}</span>
              <span>{progressLabel(run)}</span>
              <span>{formatDuration(run.started_at, run.completed_at)}</span>
            </div>

            <div className="replay-run-chips">
              {run.start_url ? <span className="badge muted">start URL</span> : null}
              {run.final_url ? <span className="badge ok">final URL</span> : null}
              {run.error ? <span className="badge err">error</span> : null}
            </div>
          </button>
        );
      })}
    </div>
  );
}

function StepStrip({
  run,
  selectedStep,
  onSelect,
}: {
  run: ReplayRun;
  selectedStep: number;
  onSelect: (index: number) => void;
}) {
  return (
    <div className="replay-step-strip">
      {run.steps.map((step, index) => {
        const active = index === selectedStep;
        return (
          <button
            key={`${run.run_id}-${step.step_index}`}
            type="button"
            className={`replay-step-pill ${active ? 'active' : ''}`}
            onClick={() => onSelect(index)}
          >
            <span className="mono">#{step.step_index}</span>
            <span>{truncate(step.action || 'unknown', 24)}</span>
          </button>
        );
      })}
    </div>
  );
}

function StepViewer({
  run,
  stepIndex,
  followLive,
  onFollowLiveChange,
  onSelectStep,
}: {
  run: ReplayRun;
  stepIndex: number;
  followLive: boolean;
  onFollowLiveChange: (value: boolean) => void;
  onSelectStep: (index: number) => void;
}) {
  const safeIndex = Math.min(Math.max(stepIndex, 0), Math.max(run.steps.length - 1, 0));
  const step = run.steps[safeIndex] ?? null;
  const screenshotSrc = step?.screenshot_b64
    ? `data:image/png;base64,${step.screenshot_b64}`
    : null;
  const lastUrl = lastKnownUrl(run);

  return (
    <div className="replay-detail-stack">
      <div className="replay-step-toolbar">
        <div className="replay-step-controls">
          <button
            type="button"
            className="btn ghost sm"
            onClick={() => {
              onFollowLiveChange(false);
              onSelectStep(Math.max(0, safeIndex - 1));
            }}
            disabled={safeIndex === 0}
          >
            ◀ Prev
          </button>
          <button
            type="button"
            className="btn ghost sm"
            onClick={() => {
              onFollowLiveChange(false);
              onSelectStep(Math.min(run.steps.length - 1, safeIndex + 1));
            }}
            disabled={safeIndex >= run.steps.length - 1}
          >
            Next ▶
          </button>
          <span className="badge muted">
            step {safeIndex + 1} / {run.steps.length}
          </span>
        </div>

        <div className="replay-step-actions">
          <button
            type="button"
            className={`btn ghost sm ${followLive ? 'is-live' : ''}`}
            onClick={() => onFollowLiveChange(!followLive)}
          >
            <Icon path={paths.activity} size={14} />
            {followLive ? 'Following live' : 'Follow live'}
          </button>
          <button
            type="button"
            className="btn ghost sm"
            onClick={() => {
              onFollowLiveChange(false);
              onSelectStep(run.steps.length - 1);
            }}
          >
            Jump to latest
          </button>
        </div>
      </div>

      <div className="replay-stage">
        <div className="replay-stage-visual">
          {screenshotSrc ? (
            <img
              src={screenshotSrc}
              alt={`Replay screenshot for step ${safeIndex + 1}`}
              className="replay-stage-image"
            />
          ) : (
            <div className="replay-stage-empty">No screenshot was captured for this step.</div>
          )}
        </div>

        <div className="replay-stage-sidebar">
          <div className="detail-grid">
            <div className="meta-tile">
              <span className="meta-label">Action</span>
              <span className="mono">{step?.action || '—'}</span>
            </div>
            <div className="meta-tile">
              <span className="meta-label">Captured</span>
              <span>{step ? formatRelative(step.ts) : '—'}</span>
              <span className="muted">{step ? formatAbsolute(step.ts) : '—'}</span>
            </div>
            <div className="meta-tile">
              <span className="meta-label">URL</span>
              <span>{step?.current_url ? truncate(step.current_url, 44) : '—'}</span>
            </div>
            <div className="meta-tile">
              <span className="meta-label">Extracted keys</span>
              <span>{formatNumber(Object.keys(step?.extracted_data ?? {}).length)}</span>
            </div>
          </div>

          <div className="replay-copy-block">
            <span className="section-label">Reasoning</span>
            <div className="info-block">
              {step?.reasoning || 'No reasoning persisted for this step.'}
            </div>
          </div>

          <div className="replay-copy-block">
            <span className="section-label">Current URL</span>
            <div className="info-block mono replay-url-block">
              {step?.current_url || lastUrl || 'No URL available'}
            </div>
          </div>

          <div className="replay-copy-block">
            <span className="section-label">Extracted data at this step</span>
            <pre className="code-block">{JSON.stringify(step?.extracted_data ?? {}, null, 2)}</pre>
          </div>
        </div>
      </div>

      <StepStrip
        run={run}
        selectedStep={safeIndex}
        onSelect={(index) => {
          onFollowLiveChange(false);
          onSelectStep(index);
        }}
      />
    </div>
  );
}

export function Replay() {
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [selectedStep, setSelectedStep] = useState(0);
  const [followLive, setFollowLive] = useState(true);

  const runsQuery = useQuery({
    queryKey: ['replay-runs'],
    queryFn: () => api.replayRuns(100),
    refetchInterval: 15_000,
  });

  const selectedSummary =
    runsQuery.data?.runs.find((run) => run.run_id === selectedId) ??
    runsQuery.data?.runs[0] ??
    null;

  const runDetail = useQuery({
    queryKey: ['replay-run', selectedSummary?.run_id ?? ''],
    queryFn: () =>
      selectedSummary ? api.replayRun(selectedSummary.run_id) : Promise.resolve(null),
    enabled: Boolean(selectedSummary),
    refetchInterval: selectedSummary?.status === 'running' ? 3_000 : false,
  });

  useEffect(() => {
    const firstId = runsQuery.data?.runs[0]?.run_id ?? null;
    if (!selectedId && firstId) {
      setSelectedId(firstId);
      setSelectedStep(0);
      setFollowLive(true);
      return;
    }
    if (selectedId && !(runsQuery.data?.runs ?? []).some((run) => run.run_id === selectedId)) {
      setSelectedId(firstId);
      setSelectedStep(0);
      setFollowLive(true);
    }
  }, [runsQuery.data, selectedId]);

  const run = runDetail.data?.run ?? null;

  useEffect(() => {
    if (!run) return;
    if (followLive) {
      setSelectedStep(Math.max(0, run.steps.length - 1));
      return;
    }
    setSelectedStep((prev) => Math.min(prev, Math.max(0, run.steps.length - 1)));
  }, [followLive, run?.run_id, run?.steps.length]);

  const runs = runsQuery.data?.runs ?? [];
  const statusCounts = runsQuery.data?.status_counts ?? {};
  const runningCount = statusCounts.running ?? 0;
  const completedCount = statusCounts.completed ?? 0;
  const failedCount = statusCounts.failed ?? 0;
  const latestUrl = run ? lastKnownUrl(run) : '';
  const selectionStatusTone = run ? statusTone(run.status) : 'muted';

  return (
    <div className="replay-page">
      <PageHeader
        eyebrow="Time Travel"
        title="Replay"
        description="Recorded run catalog, live step playback, screenshots, reasoning, and extracted data persisted into Baselithbot's replay store."
        actions={
          <div className="inline">
            <button
              type="button"
              className="btn ghost"
              disabled={runsQuery.isFetching}
              onClick={() => {
                void runsQuery.refetch();
                if (selectedSummary) void runDetail.refetch();
              }}
            >
              <Icon path={paths.refresh} size={14} />
              {runsQuery.isFetching || runDetail.isFetching ? 'Refreshing…' : 'Refresh'}
            </button>
          </div>
        }
      />

      {runsQuery.isLoading ? (
        <Skeleton height={320} />
      ) : runs.length === 0 ? (
        <EmptyState
          title="No runs recorded yet"
          description="Kick off a task from `Run Task` and Baselithbot will persist every step to the replay store automatically."
        />
      ) : (
        <>
          <Panel className="replay-command-panel">
            <div className="replay-command-grid">
              <div className="replay-command-copy">
                <div className="replay-command-head">
                  <span className="badge muted">sqlite store</span>
                  <span className="badge muted">
                    retention {formatNumber(runsQuery.data?.retention_days ?? 14)} days
                  </span>
                  <span className={`badge ${runningCount > 0 ? 'warn' : 'muted'}`}>
                    {runningCount > 0 ? `${formatNumber(runningCount)} running` : 'catalog idle'}
                  </span>
                </div>

                <h2>Recorded execution timeline</h2>
                <p>
                  Replay persists every run into SQLite so you can inspect screenshots, reasoning,
                  URLs, and extracted payloads after execution. The selected run stays linked to
                  live `run.*` updates while it is still in progress.
                </p>

                <div className="replay-mini-stats">
                  <div className="replay-mini-stat">
                    <span className="meta-label">Runs</span>
                    <strong>{formatNumber(runs.length)}</strong>
                    <span className="muted">visible catalog window</span>
                  </div>
                  <div className="replay-mini-stat">
                    <span className="meta-label">Steps</span>
                    <strong>{formatNumber(runsQuery.data?.step_totals ?? 0)}</strong>
                    <span className="muted">persisted in current slice</span>
                  </div>
                  <div className="replay-mini-stat">
                    <span className="meta-label">Latest Start</span>
                    <strong>
                      {runsQuery.data?.latest_started_ts
                        ? formatRelative(runsQuery.data.latest_started_ts)
                        : '—'}
                    </strong>
                    <span className="muted">
                      {runsQuery.data?.latest_started_ts
                        ? formatAbsolute(runsQuery.data.latest_started_ts)
                        : 'No run in catalog'}
                    </span>
                  </div>
                  <div className="replay-mini-stat">
                    <span className="meta-label">Latest Completion</span>
                    <strong>
                      {runsQuery.data?.latest_completed_ts
                        ? formatRelative(runsQuery.data.latest_completed_ts)
                        : '—'}
                    </strong>
                    <span className="muted">
                      {runsQuery.data?.latest_completed_ts
                        ? formatAbsolute(runsQuery.data.latest_completed_ts)
                        : 'No finished run yet'}
                    </span>
                  </div>
                </div>

                <div className="replay-command-footer">
                  <div className="replay-db-tile">
                    <span className="meta-label">Replay DB</span>
                    <strong>{truncate(runsQuery.data?.path ?? 'replay.sqlite', 48)}</strong>
                    <span className="mono replay-path-preview">{runsQuery.data?.path}</span>
                  </div>

                  <div className="replay-status-row">
                    <div className="replay-status-chip">
                      <span>Running</span>
                      <strong>{formatNumber(runningCount)}</strong>
                    </div>
                    <div className="replay-status-chip">
                      <span>Completed</span>
                      <strong>{formatNumber(completedCount)}</strong>
                    </div>
                    <div className="replay-status-chip">
                      <span>Failed</span>
                      <strong>{formatNumber(failedCount)}</strong>
                    </div>
                  </div>
                </div>
              </div>

              <div className="replay-selection-card">
                <div className="replay-selection-head">
                  <span className="meta-label">Selected run</span>
                  <span className={`badge ${selectionStatusTone}`}>
                    {run ? run.status : 'no selection'}
                  </span>
                </div>

                {run ? (
                  <>
                    <h3>{summarizedGoal(run.goal)}</h3>
                    <div className="replay-selection-meta">
                      <div className="replay-selection-kv">
                        <span>Run ID</span>
                        <span className="mono">{run.run_id}</span>
                      </div>
                      <div className="replay-selection-kv">
                        <span>Duration</span>
                        <span>{runDurationLabel(run)}</span>
                      </div>
                      <div className="replay-selection-kv">
                        <span>Progress</span>
                        <span>{progressLabel(run)}</span>
                      </div>
                      <div className="replay-selection-kv">
                        <span>Screenshots</span>
                        <span>{formatNumber(run.screenshot_steps)}</span>
                      </div>
                    </div>

                    <div className="replay-selection-url">
                      <span className="section-label">Last known URL</span>
                      <div className="info-block mono replay-url-block">
                        {latestUrl || 'No page URL available'}
                      </div>
                    </div>
                  </>
                ) : (
                  <div className="replay-sidecallout">
                    Select a run from the catalog to inspect its step timeline.
                  </div>
                )}
              </div>
            </div>
          </Panel>

          <section className="replay-layout">
            <Panel title="Recorded runs" tag={`${formatNumber(runs.length)} visible`}>
              <RunCatalog
                runs={runs}
                selectedId={selectedSummary?.run_id ?? null}
                onSelect={(id) => {
                  setSelectedId(id);
                  setSelectedStep(0);
                  setFollowLive(true);
                }}
              />
            </Panel>

            {runDetail.isLoading && !run ? (
              <Skeleton height={480} />
            ) : run ? (
              <div className="replay-detail-column">
                <Panel className="replay-run-summary-panel">
                  <div className="replay-run-summary">
                    <div className="replay-run-summary-copy">
                      <div className="replay-run-summary-head">
                        <span className={`badge ${statusTone(run.status)}`}>{run.status}</span>
                        <span className="badge muted mono">{run.run_id}</span>
                      </div>
                      <h2>{run.goal}</h2>
                      <p>
                        {run.status === 'running'
                          ? 'This run is still live. New steps, screenshots, and extracted data will continue to appear here.'
                          : 'Persisted execution record for post-run debugging, audit, and extraction review.'}
                      </p>

                      <div className="chip-row">
                        <span className="badge muted">{progressLabel(run)}</span>
                        <span className="badge muted">
                          {formatNumber(run.screenshot_steps)} screenshot steps
                        </span>
                        <span className="badge muted">
                          {formatNumber(run.distinct_url_count)} distinct URLs
                        </span>
                        {run.error ? <span className="badge err">terminal error</span> : null}
                      </div>
                    </div>

                    <div className="detail-grid">
                      <div className="meta-tile">
                        <span className="meta-label">Started</span>
                        <span>{formatRelative(run.started_at)}</span>
                        <span className="muted">{formatAbsolute(run.started_at)}</span>
                      </div>
                      <div className="meta-tile">
                        <span className="meta-label">Completed</span>
                        <span>
                          {run.completed_at ? formatRelative(run.completed_at) : 'Still running'}
                        </span>
                        <span className="muted">
                          {run.completed_at
                            ? formatAbsolute(run.completed_at)
                            : 'Awaiting terminal event'}
                        </span>
                      </div>
                      <div className="meta-tile">
                        <span className="meta-label">Duration</span>
                        <span>{formatDuration(run.started_at, run.completed_at)}</span>
                        <span className="muted">{progressLabel(run)}</span>
                      </div>
                      <div className="meta-tile">
                        <span className="meta-label">Final URL</span>
                        <span>{latestUrl ? truncate(latestUrl, 42) : '—'}</span>
                        <span className="muted">{latestUrl || 'No page URL captured yet'}</span>
                      </div>
                    </div>

                    {run.error ? (
                      <div className="replay-error-callout">
                        <span className="section-label">Failure reason</span>
                        <div className="info-block">{run.error}</div>
                      </div>
                    ) : null}
                  </div>
                </Panel>

                {run.steps.length === 0 ? (
                  <EmptyState
                    title="No steps captured"
                    description="The run exists, but no persisted replay step is available yet."
                  />
                ) : (
                  <Panel title="Step playback" tag={`${formatNumber(run.steps.length)} steps`}>
                    <StepViewer
                      run={run}
                      stepIndex={selectedStep}
                      followLive={followLive}
                      onFollowLiveChange={setFollowLive}
                      onSelectStep={setSelectedStep}
                    />
                  </Panel>
                )}

                <Panel title="Run output snapshot" tag="terminal extracted state">
                  <pre className="code-block">
                    {JSON.stringify(run.extracted_data ?? {}, null, 2)}
                  </pre>
                </Panel>
              </div>
            ) : (
              <EmptyState
                title="Select a run to inspect"
                description="Choose a replay record from the catalog to open its screenshots, reasoning, and extracted data."
              />
            )}
          </section>
        </>
      )}
    </div>
  );
}

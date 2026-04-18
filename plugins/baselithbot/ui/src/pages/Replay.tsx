import { useEffect, useMemo, useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { api, type ReplayRun, type ReplayRunSummary } from '../lib/api';
import { PageHeader } from '../components/PageHeader';
import { Panel } from '../components/Panel';
import { Skeleton } from '../components/Skeleton';
import { EmptyState } from '../components/EmptyState';
import { formatRelative } from '../lib/format';

function statusBadge(status: string): string {
  switch (status) {
    case 'completed':
      return 'bg-emerald-900/40 text-emerald-300';
    case 'failed':
      return 'bg-red-900/40 text-red-300';
    case 'running':
      return 'bg-sky-900/40 text-sky-300';
    default:
      return 'bg-zinc-800 text-zinc-300';
  }
}

function RunList({
  runs,
  selectedId,
  onSelect,
}: {
  runs: ReplayRunSummary[];
  selectedId: string | null;
  onSelect: (id: string) => void;
}) {
  return (
    <Panel>
      <header className="px-4 pt-4">
        <h3 className="text-sm font-semibold">Recorded runs</h3>
        <p className="text-xs text-zinc-400">
          Persisted in SQLite. 14-day retention (cron).
        </p>
      </header>
      <div className="max-h-[calc(100vh-16rem)] overflow-y-auto">
        {runs.length === 0 ? (
          <div className="px-4 pb-4 pt-2 text-xs text-zinc-500">
            No runs recorded yet.
          </div>
        ) : (
          <ul className="divide-y divide-zinc-800/60">
            {runs.map((run) => {
              const active = run.run_id === selectedId;
              return (
                <li key={run.run_id}>
                  <button
                    type="button"
                    onClick={() => onSelect(run.run_id)}
                    className={`block w-full px-4 py-3 text-left text-xs transition ${
                      active ? 'bg-zinc-900' : 'hover:bg-zinc-900/60'
                    }`}
                  >
                    <div className="flex items-center justify-between">
                      <span className="font-mono text-[10px] text-zinc-400">
                        {run.run_id.slice(0, 16)}
                      </span>
                      <span
                        className={`rounded px-2 py-0.5 text-[10px] uppercase ${statusBadge(
                          run.status,
                        )}`}
                      >
                        {run.status}
                      </span>
                    </div>
                    <div className="mt-1 line-clamp-2 text-zinc-200">
                      {run.goal}
                    </div>
                    <div className="mt-1 flex items-center gap-3 text-[10px] text-zinc-500">
                      <span>{formatRelative(run.started_at)}</span>
                      <span>{run.step_count} steps</span>
                    </div>
                  </button>
                </li>
              );
            })}
          </ul>
        )}
      </div>
    </Panel>
  );
}

function StepScrubber({ run }: { run: ReplayRun }) {
  const [idx, setIdx] = useState(0);
  useEffect(() => {
    setIdx(0);
  }, [run.run_id]);

  const steps = run.steps;
  const step = steps[idx] ?? null;

  const screenshotSrc = useMemo(() => {
    if (!step?.screenshot_b64) return null;
    return `data:image/png;base64,${step.screenshot_b64}`;
  }, [step]);

  if (steps.length === 0) {
    return (
      <Panel>
        <div className="px-4 py-6 text-xs text-zinc-500">
          No steps captured for this run.
        </div>
      </Panel>
    );
  }

  return (
    <Panel>
      <header className="flex items-center justify-between gap-4 border-b border-zinc-800/60 px-4 py-3">
        <div className="flex items-center gap-3">
          <button
            type="button"
            className="rounded border border-zinc-700 px-2 py-1 text-xs disabled:opacity-40"
            onClick={() => setIdx((i) => Math.max(0, i - 1))}
            disabled={idx === 0}
          >
            ◀
          </button>
          <input
            type="range"
            min={0}
            max={steps.length - 1}
            value={idx}
            onChange={(e) => setIdx(Number(e.target.value))}
            className="w-56"
          />
          <button
            type="button"
            className="rounded border border-zinc-700 px-2 py-1 text-xs disabled:opacity-40"
            onClick={() => setIdx((i) => Math.min(steps.length - 1, i + 1))}
            disabled={idx >= steps.length - 1}
          >
            ▶
          </button>
          <span className="font-mono text-xs text-zinc-300">
            step {idx + 1} / {steps.length}
          </span>
        </div>
        <code className="text-[10px] text-zinc-500">{run.run_id}</code>
      </header>

      <div className="grid grid-cols-1 gap-0 lg:grid-cols-[minmax(0,3fr)_minmax(0,2fr)]">
        <div className="border-r border-zinc-800/60 bg-zinc-950 p-3">
          {screenshotSrc ? (
            <img
              src={screenshotSrc}
              alt={`step ${idx + 1} screenshot`}
              className="max-h-[60vh] w-full rounded object-contain"
            />
          ) : (
            <div className="flex h-48 items-center justify-center text-xs text-zinc-500">
              No screenshot captured for this step.
            </div>
          )}
        </div>
        <div className="space-y-3 p-4 text-xs">
          <div>
            <div className="text-[10px] uppercase text-zinc-500">Action</div>
            <div className="mt-1 font-mono text-zinc-200">{step?.action}</div>
          </div>
          <div>
            <div className="text-[10px] uppercase text-zinc-500">Reasoning</div>
            <div className="mt-1 whitespace-pre-wrap text-zinc-300">
              {step?.reasoning || '—'}
            </div>
          </div>
          <div>
            <div className="text-[10px] uppercase text-zinc-500">URL</div>
            <div className="mt-1 break-all font-mono text-sky-400">
              {step?.current_url || '—'}
            </div>
          </div>
          <div>
            <div className="text-[10px] uppercase text-zinc-500">
              Extracted so far
            </div>
            <pre className="mt-1 max-h-40 overflow-auto rounded bg-zinc-950 p-2 text-[11px] text-zinc-300">
              {JSON.stringify(step?.extracted_data ?? {}, null, 2)}
            </pre>
          </div>
          <div className="text-[10px] text-zinc-500">
            captured {step ? formatRelative(step.ts) : '—'}
          </div>
        </div>
      </div>
    </Panel>
  );
}

export function Replay() {
  const [selectedId, setSelectedId] = useState<string | null>(null);

  const runsQuery = useQuery({
    queryKey: ['replay-runs'],
    queryFn: () => api.replayRuns(100),
    refetchInterval: 5_000,
  });

  const runDetail = useQuery({
    queryKey: ['replay-run', selectedId],
    queryFn: () => (selectedId ? api.replayRun(selectedId) : Promise.resolve(null)),
    enabled: Boolean(selectedId),
  });

  useEffect(() => {
    if (selectedId) return;
    const first = runsQuery.data?.runs[0]?.run_id;
    if (first) setSelectedId(first);
  }, [runsQuery.data, selectedId]);

  return (
    <div className="space-y-4">
      <PageHeader
        title="Replay"
        description="Time-travel debugger for past agent runs. Every Observe → Plan → Act step captured with screenshot, reasoning, and URL."
      />
      {runsQuery.isLoading ? (
        <Skeleton height={256} />
      ) : (runsQuery.data?.runs ?? []).length === 0 ? (
        <EmptyState
          title="No runs recorded yet"
          description="Kick off a task from the Run page — each step is persisted automatically."
        />
      ) : (
        <div className="grid grid-cols-1 gap-4 lg:grid-cols-[minmax(0,1fr)_minmax(0,2fr)]">
          <RunList
            runs={runsQuery.data?.runs ?? []}
            selectedId={selectedId}
            onSelect={setSelectedId}
          />
          {runDetail.isLoading ? (
            <Skeleton height={384} />
          ) : runDetail.data ? (
            <StepScrubber run={runDetail.data.run} />
          ) : (
            <EmptyState title="Select a run to replay" />
          )}
        </div>
      )}
    </div>
  );
}

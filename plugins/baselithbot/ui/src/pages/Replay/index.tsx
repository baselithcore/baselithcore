import { useEffect, useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { EmptyState } from '../../components/EmptyState';
import { PageHeader } from '../../components/PageHeader';
import { Panel } from '../../components/Panel';
import { Skeleton } from '../../components/Skeleton';
import { api } from '../../lib/api';
import { formatNumber } from '../../lib/format';
import { Icon, paths } from '../../lib/icons';
import { RunCatalog } from './components';
import { lastKnownUrl, statusTone } from './helpers';
import { CommandPanel } from './sections/CommandPanel';
import { RunSummary } from './sections/RunSummary';
import { StepViewer } from './sections/StepViewer';

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
          <CommandPanel
            data={runsQuery.data}
            runs={runs}
            run={run}
            latestUrl={latestUrl}
            selectionStatusTone={selectionStatusTone}
            runningCount={runningCount}
            completedCount={completedCount}
            failedCount={failedCount}
          />

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
                <RunSummary run={run} latestUrl={latestUrl} />

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

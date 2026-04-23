import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useSearchParams } from 'react-router-dom';
import { api, type RunTaskRequest } from '../../lib/api';
import { useDashboardEvents } from '../../lib/sse';
import { PageHeader } from '../../components/PageHeader';
import { useToasts } from '../../components/ToastProvider';
import { Icon, paths } from '../../lib/icons';
import { createRunId } from './helpers';
import { HistoryExtracted } from './sections/HistoryExtracted';
import { LiveRun } from './sections/LiveRun';
import { RecentRuns } from './sections/RecentRuns';
import { TaskForm } from './sections/TaskForm';
import { Timeline } from './sections/Timeline';

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
  const goalInputRef = useRef<HTMLTextAreaElement | null>(null);

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

  const handleNewTask = useCallback(() => {
    const hasDraft =
      goal.trim().length > 0 || startUrl.trim().length > 0 || extract.trim().length > 0;
    if (mutation.isPending) {
      const ok = window.confirm(
        'A task is still being dispatched. Start a new task anyway? The running task will continue on the server.'
      );
      if (!ok) return;
    } else if (hasDraft) {
      const ok = window.confirm('Discard the current draft and start a new task?');
      if (!ok) return;
    }
    setGoal('');
    setStartUrl('');
    setExtract('');
    setMaxSteps(20);
    setErrorMsg(null);
    setSelectedRunId(null);
    const next = new URLSearchParams(searchParams);
    next.delete('run');
    setSearchParams(next, { replace: true });
    queueMicrotask(() => goalInputRef.current?.focus());
  }, [goal, startUrl, extract, mutation.isPending, searchParams, setSearchParams]);

  useEffect(() => {
    function onKey(e: KeyboardEvent) {
      if (e.key !== 'n' && e.key !== 'N') return;
      if (e.ctrlKey || e.metaKey || e.altKey) return;
      const target = e.target as HTMLElement | null;
      if (
        target &&
        (target.tagName === 'INPUT' || target.tagName === 'TEXTAREA' || target.isContentEditable)
      ) {
        return;
      }
      e.preventDefault();
      handleNewTask();
    }
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [handleNewTask]);

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 24 }}>
      <PageHeader
        eyebrow="Agent"
        title="Run an autonomous task"
        description="Dispatch a live Observe → Plan → Act run with progress tracking, current preview, execution timeline, and recent run history."
        actions={
          <button
            type="button"
            className="btn primary"
            onClick={handleNewTask}
            aria-label="Start a new task (shortcut: N)"
            title="Start a new task · press N"
          >
            <Icon path={paths.plus} size={14} />
            New task
            <kbd className="kbd">N</kbd>
          </button>
        }
      />

      <section className="grid grid-split-1-2">
        <TaskForm
          ref={goalInputRef}
          goal={goal}
          setGoal={setGoal}
          startUrl={startUrl}
          setStartUrl={setStartUrl}
          maxSteps={maxSteps}
          setMaxSteps={setMaxSteps}
          extract={extract}
          setExtract={setExtract}
          disabled={disabled}
          isPending={mutation.isPending}
          onSubmit={() =>
            mutation.mutate({
              run_id: createRunId(),
              goal: goal.trim(),
              start_url: startUrl.trim() || null,
              max_steps: maxSteps,
              extract_fields: extract
                .split(',')
                .map((s) => s.trim())
                .filter(Boolean),
            })
          }
          onReset={() => {
            setGoal('');
            setStartUrl('');
            setExtract('');
            setMaxSteps(20);
            setErrorMsg(null);
          }}
        />

        <LiveRun
          selectedRun={selectedRun}
          errorMsg={errorMsg}
          isPending={mutation.isPending}
          stepRatio={stepRatio}
        />
      </section>

      <section className="grid grid-split-1-2">
        <RecentRuns
          runs={recentRuns.data?.runs ?? []}
          selectedRunId={selectedRunId}
          setSelectedRunId={setSelectedRunId}
        />
        <Timeline
          selectedRunId={selectedRunId}
          runEvents={runEvents}
          selectedRunPresent={Boolean(selectedRun)}
        />
      </section>

      {selectedRun && <HistoryExtracted selectedRun={selectedRun} />}
    </div>
  );
}

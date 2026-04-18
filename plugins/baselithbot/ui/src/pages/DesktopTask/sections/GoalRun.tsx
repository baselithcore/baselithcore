import { useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { Link } from 'react-router-dom';
import { EmptyState } from '../../../components/EmptyState';
import { Panel } from '../../../components/Panel';
import { useToasts } from '../../../components/ToastProvider';
import { api, type DesktopToolPolicy, type RunTaskState } from '../../../lib/api';
import { truncate } from '../../../lib/format';
import { Icon, paths } from '../../../lib/icons';

export function GoalRunSection({ policy }: { policy: DesktopToolPolicy }) {
  const qc = useQueryClient();
  const { push } = useToasts();

  const [goalText, setGoalText] = useState('');
  const [goalMaxSteps, setGoalMaxSteps] = useState(12);
  const [activeDesktopRunId, setActiveDesktopRunId] = useState<string | null>(null);

  const desktopRunDetail = useQuery({
    queryKey: ['desktopTaskById', activeDesktopRunId],
    queryFn: () => api.desktopTaskById(activeDesktopRunId!),
    enabled: !!activeDesktopRunId,
    refetchInterval: (query) => (query.state.data?.run.status === 'running' ? 1_500 : 6_000),
  });
  const activeDesktopRun: RunTaskState | null = desktopRunDetail.data?.run ?? null;

  const desktopCancelMutation = useMutation({
    mutationFn: (runId: string) => api.desktopTaskCancel(runId),
    onSuccess: (data) => {
      push({
        tone: 'success',
        title: 'Desktop task cancel requested',
        description: `run ${data.run_id} will stop at the next safe boundary`,
      });
      if (activeDesktopRunId) {
        qc.invalidateQueries({ queryKey: ['desktopTaskById', activeDesktopRunId] });
      }
    },
    onError: (err: unknown) => {
      const message = err instanceof Error ? err.message : String(err);
      push({ tone: 'error', title: 'Cancel failed', description: message });
    },
  });

  const desktopTaskMutation = useMutation({
    mutationFn: (payload: { goal: string; max_steps: number }) => api.desktopTaskDispatch(payload),
    onSuccess: (data) => {
      setActiveDesktopRunId(data.run_id);
      push({
        tone: 'success',
        title: 'Desktop task launched',
        description: `run ${data.run_id} dispatched with ${goalMaxSteps} max steps`,
      });
      qc.invalidateQueries({ queryKey: ['desktopTaskById', data.run_id] });
    },
    onError: (err: unknown) => {
      const message = err instanceof Error ? err.message : String(err);
      push({ tone: 'error', title: 'Desktop task dispatch failed', description: message });
    },
  });

  const goalPending = desktopTaskMutation.isPending || activeDesktopRun?.status === 'running';
  const goalDisabled =
    !policy.enabled ||
    goalText.trim().length === 0 ||
    desktopTaskMutation.isPending ||
    Boolean(activeDesktopRun && activeDesktopRun.status === 'running');

  return (
    <section className="grid grid-split-2-1">
      <Panel title="Natural-language goal" tag={activeDesktopRun ? activeDesktopRun.status : 'idle'}>
        <p className="muted" style={{ fontSize: 13, marginBottom: 12 }}>
          Describe an OS-level task in plain language — the vision model plans the exact shell /
          mouse / keyboard / filesystem calls and executes them under the current Computer Use
          policy. Example:{' '}
          <span className="mono">"open Spotify and start the playlist Focus"</span>.
        </p>
        <form
          onSubmit={(event) => {
            event.preventDefault();
            if (goalDisabled) return;
            desktopTaskMutation.mutate({
              goal: goalText.trim(),
              max_steps: goalMaxSteps,
            });
          }}
          style={{ display: 'flex', flexDirection: 'column', gap: 12 }}
        >
          <div className="form-row">
            <label htmlFor="desk-goal">Goal</label>
            <textarea
              id="desk-goal"
              className="textarea"
              rows={3}
              maxLength={1500}
              value={goalText}
              onChange={(event) => setGoalText(event.target.value)}
              placeholder="open Spotify and start the playlist Focus"
            />
          </div>
          <div className="inline" style={{ gap: 10, alignItems: 'flex-end' }}>
            <div className="form-row" style={{ flex: '0 0 140px' }}>
              <label htmlFor="desk-steps">Max steps</label>
              <input
                id="desk-steps"
                className="input"
                type="number"
                min={1}
                max={30}
                value={goalMaxSteps}
                onChange={(event) =>
                  setGoalMaxSteps(Math.max(1, Math.min(30, Number(event.target.value) || 12)))
                }
              />
            </div>
            <div style={{ flex: 1 }} />
            <button
              type="button"
              className="btn ghost"
              disabled={goalPending}
              onClick={() => {
                setGoalText('');
                setGoalMaxSteps(12);
                setActiveDesktopRunId(null);
              }}
            >
              Reset
            </button>
            <button type="submit" className="btn primary" disabled={goalDisabled}>
              {goalPending ? (
                <>
                  <span className="spin">
                    <Icon path={paths.refresh} size={14} />
                  </span>
                  Running…
                </>
              ) : (
                <>
                  <Icon path={paths.play} size={14} />
                  Launch desktop agent
                </>
              )}
            </button>
          </div>
        </form>
        {!policy.enabled && (
          <div className="computer-warning-list" style={{ marginTop: 12 }}>
            <div className="computer-warning-item">
              <Icon path={paths.shieldOff} size={14} />
              <span>
                Master switch is OFF — the agent cannot invoke any tool. Enable it on the{' '}
                <Link to="/computer-use">Computer Use</Link> page first.
              </span>
            </div>
          </div>
        )}
      </Panel>

      <Panel
        title="Agent run"
        tag={
          activeDesktopRun ? `${activeDesktopRun.steps_taken}/${activeDesktopRun.max_steps}` : '—'
        }
      >
        {!activeDesktopRun ? (
          <EmptyState
            title="No active run"
            description="Dispatch a goal on the left to see live Observe → Plan → Act telemetry here."
          />
        ) : (
          <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
            <div className="inline">
              <span
                className={`badge ${
                  activeDesktopRun.status === 'completed'
                    ? 'ok'
                    : activeDesktopRun.status === 'failed'
                      ? 'err'
                      : 'warn'
                }`}
              >
                {activeDesktopRun.status}
              </span>
              <span className="badge muted mono">{activeDesktopRun.run_id}</span>
              {activeDesktopRun.status === 'running' && (
                <button
                  type="button"
                  className="btn ghost"
                  onClick={() => {
                    if (!activeDesktopRunId) return;
                    if (
                      window.confirm(
                        'Stop the running desktop task? The current tool call will finish, then the agent aborts.'
                      )
                    ) {
                      desktopCancelMutation.mutate(activeDesktopRunId);
                    }
                  }}
                  disabled={desktopCancelMutation.isPending}
                  aria-label="Stop desktop task"
                  title="Stop this run at the next safe boundary"
                  style={{ marginLeft: 'auto' }}
                >
                  <Icon path={paths.x} size={14} />
                  {desktopCancelMutation.isPending ? 'Stopping…' : 'Stop'}
                </button>
              )}
              <span className="badge muted">
                step {activeDesktopRun.steps_taken} / {activeDesktopRun.max_steps}
              </span>
            </div>
            <div className="info-block" style={{ fontSize: 13, lineHeight: 1.45 }}>
              <strong style={{ display: 'block', marginBottom: 4 }}>Goal</strong>
              {activeDesktopRun.goal}
            </div>
            {activeDesktopRun.last_action && (
              <div className="info-block" style={{ fontSize: 13 }}>
                <strong style={{ display: 'block', marginBottom: 4 }}>Last action</strong>
                <span className="mono">{activeDesktopRun.last_action}</span>
                {activeDesktopRun.last_reasoning && (
                  <div className="muted" style={{ marginTop: 4 }}>
                    {activeDesktopRun.last_reasoning}
                  </div>
                )}
              </div>
            )}
            {activeDesktopRun.error && <div className="run-error">{activeDesktopRun.error}</div>}
            {activeDesktopRun.last_screenshot_b64 && (
              <img
                className="screenshot"
                alt="Agent screenshot"
                src={`data:image/jpeg;base64,${activeDesktopRun.last_screenshot_b64}`}
              />
            )}
            {activeDesktopRun.history.length > 0 && (
              <div className="trace" style={{ maxHeight: 240, overflow: 'auto' }}>
                {activeDesktopRun.history.slice(-12).map((line, idx) => (
                  <div key={`${activeDesktopRun.run_id}-h-${idx}`} className="step">
                    <span className="num">#{idx + 1}</span>
                    <span className="text mono" style={{ fontSize: 12 }}>
                      {truncate(line, 180)}
                    </span>
                  </div>
                ))}
              </div>
            )}
          </div>
        )}
      </Panel>
    </section>
  );
}

import { useMemo } from 'react';
import { useQueries } from '@tanstack/react-query';
import { Link } from 'react-router-dom';
import { EmptyState } from '../../components/EmptyState';
import { api, type RunTaskState, type Session, type SessionMessage } from '../../lib/api';
import { formatAbsolute, formatRelative, truncate } from '../../lib/format';
import { Icon, paths } from '../../lib/icons';
import { badgeTone, roleTone, type SessionRunHint } from './helpers';

export function ConnectionPill({ state }: { state: 'connecting' | 'open' | 'closed' | 'error' }) {
  const config =
    state === 'open'
      ? { label: 'Live SSE', tone: 'ok' }
      : state === 'connecting'
        ? { label: 'Connecting', tone: 'warn' }
        : state === 'closed'
          ? { label: 'Offline', tone: 'down' }
          : { label: 'Reconnect', tone: 'warn' };

  return (
    <span className={`pill ${config.tone}`}>
      <span className="dot" aria-hidden />
      {config.label}
    </span>
  );
}

export function SessionRow({
  session,
  active,
  onSelect,
}: {
  session: Session;
  active: boolean;
  onSelect: () => void;
}) {
  return (
    <button type="button" className={`select-row ${active ? 'active' : ''}`} onClick={onSelect}>
      <div className="select-row-head">
        <span className="mono" style={{ fontSize: 12, color: 'var(--ink-100)' }}>
          {session.title || session.id}
        </span>
        {session.primary && <span className="badge ok">primary</span>}
      </div>
      <div className="mono" style={{ fontSize: 10, color: 'var(--ink-400)' }}>
        {session.id}
      </div>
      <div style={{ fontSize: 12, color: 'var(--ink-300)' }}>
        {formatRelative(session.last_active)} · created {formatAbsolute(session.created_at)}
      </div>
    </button>
  );
}

export function MetaTile({ label, value }: { label: string; value: string }) {
  return (
    <div className="meta-tile">
      <span className="meta-label">{label}</span>
      <span>{value}</span>
    </div>
  );
}

export function SessionRunCard({
  hint,
  run,
  error,
}: {
  hint: SessionRunHint;
  run: RunTaskState | null;
  error: string | null;
}) {
  const status = run?.status ?? hint.status ?? 'unknown';
  const tone = badgeTone(status);

  return (
    <div className="info-block" style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
      <div className="inline" style={{ justifyContent: 'space-between', flexWrap: 'wrap' }}>
        <div className="inline" style={{ gap: 8, flexWrap: 'wrap' }}>
          <span className={`badge ${tone}`}>{status}</span>
          <span className="badge muted">{hint.kind ?? 'task'}</span>
          <span className="mono" style={{ fontSize: 11, color: 'var(--ink-300)' }}>
            run {hint.runId}
          </span>
        </div>
        <Link
          to={`/run?run=${encodeURIComponent(hint.runId)}`}
          className="btn xs"
          style={{ textDecoration: 'none' }}
        >
          <Icon path={paths.play} size={12} />
          Open in Run Task
        </Link>
      </div>

      {run && (
        <>
          <div className="detail-grid">
            <MetaTile label="Steps" value={`${run.steps_taken}/${run.max_steps}`} />
            <MetaTile label="Started" value={formatAbsolute(run.started_at)} />
            <MetaTile
              label="Current URL"
              value={truncate(run.current_url || run.final_url || '—', 56)}
            />
            <MetaTile label="Last action" value={truncate(run.last_action || 'waiting', 56)} />
          </div>
          {run.last_reasoning && <div className="muted">{truncate(run.last_reasoning, 280)}</div>}
          {run.last_screenshot_b64 && (
            <img
              className="screenshot"
              alt={`Latest screenshot for run ${hint.runId}`}
              src={`data:image/png;base64,${run.last_screenshot_b64}`}
            />
          )}
          {run.error && <div className="run-error">{run.error}</div>}
        </>
      )}

      {!run && !error && (
        <div className="muted">Tracker details are still being collected for this linked run.</div>
      )}

      {error && <div className="run-error">{error}</div>}
    </div>
  );
}

export function MessageList({ messages }: { messages: SessionMessage[] }) {
  const runStatusById = useMemo(() => {
    const map = new Map<string, string | null>();
    for (const message of messages) {
      const meta = message.metadata || {};
      const rid = typeof meta.run_id === 'string' ? meta.run_id : null;
      if (!rid) continue;
      map.set(rid, typeof meta.status === 'string' ? meta.status : null);
    }
    return map;
  }, [messages]);

  const runningRunIds = useMemo(
    () =>
      Array.from(runStatusById.entries())
        .filter(([, status]) => status === 'running')
        .map(([runId]) => runId),
    [runStatusById]
  );

  const runStates = useQueries({
    queries: runningRunIds.map((rid) => ({
      queryKey: ['runTaskById', rid],
      queryFn: () => api.runTaskById(rid),
      refetchInterval: 2_500,
      retry: false,
    })),
  });

  const stateByRunId = useMemo(() => {
    const map = new Map<string, RunTaskState>();
    runStates.forEach((query, idx) => {
      const rid = runningRunIds[idx];
      if (query.data?.run && rid) map.set(rid, query.data.run);
    });
    return map;
  }, [runStates, runningRunIds]);

  if (messages.length === 0) {
    return (
      <EmptyState
        title="No messages"
        description="Send a goal or slash command via the composer below."
      />
    );
  }

  return (
    <div className="stack-list scroll" style={{ maxHeight: 520 }}>
      {messages.map((message, index) => {
        const meta = message.metadata || {};
        const tone = roleTone(message.role);
        const rid = typeof meta.run_id === 'string' ? meta.run_id : null;
        const kind = typeof meta.kind === 'string' ? meta.kind : null;
        const status = typeof meta.status === 'string' ? meta.status : null;
        const live = rid ? stateByRunId.get(rid) : null;

        return (
          <div
            key={index}
            className="meta-tile"
            style={{
              gap: 8,
              borderLeft: `3px solid ${
                tone === 'user'
                  ? 'var(--accent-300, #7aa2ff)'
                  : tone === 'assistant'
                    ? 'var(--ok-400, #5ad19c)'
                    : 'var(--ink-500, #4b5260)'
              }`,
              paddingLeft: 10,
            }}
          >
            <div
              style={{
                display: 'flex',
                justifyContent: 'space-between',
                gap: 12,
                fontSize: 11,
                color: 'var(--ink-400)',
              }}
              className="mono"
            >
              <span style={{ textTransform: 'uppercase', letterSpacing: '0.04em' }}>
                {message.role}
                {kind ? ` · ${kind}` : ''}
              </span>
              <span>{formatRelative(message.ts)}</span>
            </div>
            <div className="mono" style={{ fontSize: 12, whiteSpace: 'pre-wrap' }}>
              {message.content}
            </div>
            {rid && <RunBadge runId={rid} fallbackStatus={status} live={live ?? null} />}
          </div>
        );
      })}
    </div>
  );
}

function RunBadge({
  runId,
  fallbackStatus,
  live,
}: {
  runId: string;
  fallbackStatus: string | null;
  live: RunTaskState | null;
}) {
  const status = live?.status ?? fallbackStatus ?? 'unknown';
  const tone = badgeTone(status);

  return (
    <div
      className="inline"
      style={{ gap: 8, fontSize: 11, color: 'var(--ink-300)', flexWrap: 'wrap' }}
    >
      <span className={`badge ${tone}`}>{status}</span>
      <span className="mono">run {runId}</span>
      <Link
        to={`/run?run=${encodeURIComponent(runId)}`}
        className="btn xs"
        style={{ textDecoration: 'none' }}
      >
        <Icon path={paths.play} size={12} />
        Open run
      </Link>
      {live && (
        <>
          <span>
            step {live.steps_taken}/{live.max_steps}
          </span>
          {live.last_action && <span>· {truncate(live.last_action, 56)}</span>}
          {live.current_url && (
            <span className="mono" title={live.current_url}>
              · {truncate(live.current_url, 48)}
            </span>
          )}
        </>
      )}
    </div>
  );
}

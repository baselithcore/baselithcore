import { useDeferredValue, useEffect, useMemo, useState } from 'react';
import { Link, useSearchParams } from 'react-router-dom';
import { useMutation, useQueries, useQuery, useQueryClient } from '@tanstack/react-query';
import {
  api,
  type DashboardEvent,
  type RunTaskState,
  type Session,
  type SessionMessage,
} from '../lib/api';
import { useDashboardEvents } from '../lib/sse';
import { useConfirm } from '../components/ConfirmProvider';
import { EmptyState } from '../components/EmptyState';
import { PageHeader } from '../components/PageHeader';
import { Panel } from '../components/Panel';
import { Skeleton } from '../components/Skeleton';
import { useToasts } from '../components/ToastProvider';
import { Icon, paths } from '../lib/icons';
import { formatAbsolute, formatNumber, formatRelative, truncate } from '../lib/format';

type SortKey = 'activity' | 'title' | 'created';

interface SessionRunHint {
  runId: string;
  status: string | null;
  kind: string | null;
}

export function Sessions() {
  const qc = useQueryClient();
  const confirm = useConfirm();
  const { push } = useToasts();
  const { events, state: eventState } = useDashboardEvents(320);
  const [searchParams, setSearchParams] = useSearchParams();

  const [title, setTitle] = useState('');
  const [primary, setPrimary] = useState(false);
  const [selected, setSelected] = useState<string | null>(null);
  const [msg, setMsg] = useState('');
  const [search, setSearch] = useState('');
  const [sort, setSort] = useState<SortKey>('activity');
  const deferredSearch = useDeferredValue(search);

  const list = useQuery({
    queryKey: ['sessions'],
    queryFn: api.sessions,
    refetchInterval: eventState === 'open' ? false : 10_000,
  });

  const sessions = list.data?.sessions ?? [];

  useEffect(() => {
    const requestedSessionId = searchParams.get('session');
    const known = new Set(sessions.map((session) => session.id));

    if (requestedSessionId && known.has(requestedSessionId)) {
      if (selected !== requestedSessionId) setSelected(requestedSessionId);
      return;
    }

    if (sessions.length === 0) {
      if (selected !== null) setSelected(null);
      return;
    }

    if (!selected || !known.has(selected)) {
      const next = pickDefaultSessionId(sessions);
      if (next && selected !== next) setSelected(next);
    }
  }, [searchParams, selected, sessions]);

  useEffect(() => {
    const current = searchParams.get('session');
    if ((current ?? null) === selected) return;
    const next = new URLSearchParams(searchParams);
    if (selected) next.set('session', selected);
    else next.delete('session');
    setSearchParams(next, { replace: true });
  }, [searchParams, selected, setSearchParams]);

  const selectedSession = useMemo(
    () => sessions.find((session) => session.id === selected) ?? null,
    [selected, sessions]
  );

  const filteredSessions = useMemo(() => {
    const needle = deferredSearch.trim().toLowerCase();
    const filtered = sessions.filter((session) => {
      if (!needle) return true;
      return (
        session.id.toLowerCase().includes(needle) ||
        (session.title || '').toLowerCase().includes(needle)
      );
    });
    return sortSessions(filtered, sort);
  }, [deferredSearch, sessions, sort]);

  const history = useQuery({
    queryKey: ['sessionHistory', selected],
    queryFn: () => (selected ? api.sessionHistory(selected, 200) : Promise.resolve(null)),
    enabled: !!selected,
    refetchInterval: selected && eventState !== 'open' ? 5_000 : false,
  });

  const latestRunHint = useMemo(
    () => extractLatestRun(history.data?.messages ?? []),
    [history.data?.messages]
  );

  const latestRun = useQuery({
    queryKey: ['runTaskById', latestRunHint?.runId],
    queryFn: () => api.runTaskById(latestRunHint!.runId),
    enabled: !!latestRunHint?.runId,
    retry: false,
    refetchInterval: (query) => {
      const status = query.state.data?.run.status ?? latestRunHint?.status;
      if (status === 'running') return 2_500;
      return eventState === 'open' ? false : 10_000;
    },
  });

  const selectedSessionEvents = useMemo(() => {
    if (!selected) return [];
    return events
      .filter((event) => readEventSessionId(event) === selected)
      .slice()
      .reverse()
      .slice(0, 8);
  }, [events, selected]);

  const create = useMutation({
    mutationFn: () => api.createSession(title.trim(), primary),
    onSuccess: (session) => {
      setTitle('');
      setPrimary(false);
      setSelected(session.id);
      qc.invalidateQueries({ queryKey: ['sessions'] });
      push({
        tone: 'success',
        title: 'Session created',
        description: `${session.title || session.id} is ready for messages.`,
      });
    },
    onError: (err: unknown) =>
      push({
        tone: 'error',
        title: 'Session creation failed',
        description: err instanceof Error ? err.message : String(err),
      }),
  });

  const reset = useMutation({
    mutationFn: (id: string) => api.resetSession(id),
    onSuccess: (_, id) => {
      qc.invalidateQueries({ queryKey: ['sessionHistory', id] });
      push({
        tone: 'success',
        title: 'History reset',
        description: `Session ${id.slice(0, 8)} was cleared.`,
      });
    },
    onError: (err: unknown) =>
      push({
        tone: 'error',
        title: 'Reset failed',
        description: err instanceof Error ? err.message : String(err),
      }),
  });

  const del = useMutation({
    mutationFn: (id: string) => api.deleteSession(id),
    onSuccess: (_, id) => {
      const remaining = sessions.filter((session) => session.id !== id);
      setSelected((current) => (current === id ? pickDefaultSessionId(remaining) : current));
      qc.removeQueries({ queryKey: ['sessionHistory', id] });
      qc.invalidateQueries({ queryKey: ['sessions'] });
      push({
        tone: 'success',
        title: 'Session deleted',
        description: `Session ${id.slice(0, 8)} was removed.`,
      });
    },
    onError: (err: unknown) =>
      push({
        tone: 'error',
        title: 'Delete failed',
        description: err instanceof Error ? err.message : String(err),
      }),
  });

  const send = useMutation({
    mutationFn: ({ id, content }: { id: string; content: string }) => api.sendMessage(id, content),
    onSuccess: (data, vars) => {
      setMsg('');
      qc.invalidateQueries({ queryKey: ['sessionHistory', vars.id] });
      qc.invalidateQueries({ queryKey: ['sessions'] });
      if (data.reply.kind === 'task') {
        push({
          tone: 'success',
          title: 'Task launched',
          description: `Run ${data.reply.run_id} is now attached to this session.`,
        });
      }
    },
    onError: (err: unknown) =>
      push({
        tone: 'error',
        title: 'Send failed',
        description: err instanceof Error ? err.message : String(err),
      }),
  });

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 24 }}>
      <PageHeader
        eyebrow="Conversations"
        title="Sessions"
        description={`${formatNumber(sessions.length)} bounded histories for agent conversations, slash commands, and linked autonomous runs.`}
      />

      <div className="inline" style={{ justifyContent: 'space-between', flexWrap: 'wrap' }}>
        <ConnectionPill state={eventState} />
        {latestRunHint && (
          <Link
            to={`/run?run=${encodeURIComponent(latestRunHint.runId)}`}
            className="btn sm"
            style={{ textDecoration: 'none' }}
          >
            <Icon path={paths.play} size={12} />
            Open linked run
          </Link>
        )}
      </div>

      <section className="grid grid-split-1-2">
        <div style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>
          <Panel title="New session">
            <form
              style={{ display: 'flex', flexDirection: 'column', gap: 12 }}
              onSubmit={(event) => {
                event.preventDefault();
                if (!create.isPending) create.mutate();
              }}
            >
              <div className="form-row">
                <label htmlFor="stitle">Title</label>
                <input
                  id="stitle"
                  className="input"
                  placeholder="research-run-42"
                  value={title}
                  onChange={(event) => setTitle(event.target.value)}
                />
              </div>
              <label
                style={{
                  display: 'flex',
                  alignItems: 'center',
                  gap: 8,
                  fontSize: 12,
                  color: 'var(--ink-300)',
                }}
              >
                <input
                  type="checkbox"
                  checked={primary}
                  onChange={(event) => setPrimary(event.target.checked)}
                />
                Mark as primary
              </label>
              <button type="submit" className="btn primary" disabled={create.isPending}>
                <Icon path={paths.plus} size={14} />
                Create session
              </button>
            </form>
          </Panel>

          <Panel
            title="All sessions"
            tag={`${formatNumber(sessions.length)} total · ${eventState === 'open' ? 'live' : 'fallback'}`}
          >
            {list.isLoading && <Skeleton height={120} />}
            {list.data && sessions.length === 0 && (
              <EmptyState
                title="No sessions yet"
                description="Create one to start a bounded conversation history with the agent."
              />
            )}
            {list.data && sessions.length > 0 && (
              <>
                <div className="toolbar">
                  <input
                    className="input toolbar-grow"
                    placeholder="Filter sessions…"
                    value={search}
                    onChange={(event) => setSearch(event.target.value)}
                  />
                  <select
                    className="select"
                    value={sort}
                    onChange={(event) => setSort(event.target.value as SortKey)}
                  >
                    <option value="activity">sort: recent activity</option>
                    <option value="title">sort: title</option>
                    <option value="created">sort: created</option>
                  </select>
                </div>

                {filteredSessions.length === 0 ? (
                  <EmptyState
                    title="No sessions match"
                    description="Adjust the filter to inspect another conversation history."
                  />
                ) : (
                  <div className="stack-list">
                    {filteredSessions.map((session) => (
                      <SessionRow
                        key={session.id}
                        session={session}
                        active={session.id === selected}
                        onSelect={() => setSelected(session.id)}
                      />
                    ))}
                  </div>
                )}
              </>
            )}
          </Panel>
        </div>

        <Panel
          title={
            selectedSession ? `History · ${selectedSession.title || selectedSession.id}` : 'History'
          }
          tag={selected && history.data ? `${history.data.messages.length} msgs` : ''}
        >
          {!selected && (
            <EmptyState
              title="Pick a session"
              description="Select a session on the left to inspect its history, live activity, and linked runs."
            />
          )}

          {selected && history.isLoading && <Skeleton height={260} />}

          {selected && history.data && (
            <div style={{ display: 'flex', flexDirection: 'column', gap: 14 }}>
              {selectedSession && (
                <>
                  <div className="detail-grid">
                    <MetaTile label="Primary" value={selectedSession.primary ? 'yes' : 'no'} />
                    <MetaTile label="Created" value={formatAbsolute(selectedSession.created_at)} />
                    <MetaTile
                      label="Last active"
                      value={formatAbsolute(selectedSession.last_active)}
                    />
                    <MetaTile
                      label="Updates"
                      value={eventState === 'open' ? 'live sse' : 'polling fallback'}
                    />
                  </div>

                  {selectedSession.sandbox && (
                    <div className="stack-section">
                      <div className="section-label">Sandbox</div>
                      <pre className="code-block">
                        {JSON.stringify(selectedSession.sandbox, null, 2)}
                      </pre>
                    </div>
                  )}
                </>
              )}

              {latestRunHint && (
                <div className="stack-section">
                  <div className="section-label">Linked run</div>
                  <SessionRunCard
                    hint={latestRunHint}
                    run={latestRun.data?.run ?? null}
                    error={latestRun.error instanceof Error ? latestRun.error.message : null}
                  />
                </div>
              )}

              {selectedSessionEvents.length > 0 && (
                <div className="stack-section">
                  <div className="section-label">Recent activity</div>
                  <div className="scroll" style={{ maxHeight: 240 }}>
                    {selectedSessionEvents.map((event, index) => (
                      <div key={`${event.ts}-${index}`} className="event-row">
                        <span className="bullet" />
                        <div style={{ flex: 1, minWidth: 0 }}>
                          <div className="meta">
                            <span>{event.type}</span>
                            <span>{formatRelative(event.ts)}</span>
                          </div>
                          <div className="body">{truncate(JSON.stringify(event.payload), 220)}</div>
                        </div>
                      </div>
                    ))}
                  </div>
                </div>
              )}

              <div className="stack-section">
                <div className="section-label">Messages</div>
                <MessageList messages={history.data.messages} />
              </div>

              <form
                className="inline"
                onSubmit={(event) => {
                  event.preventDefault();
                  if (!selected || !msg.trim() || send.isPending) return;
                  send.mutate({ id: selected, content: msg.trim() });
                }}
              >
                <input
                  className="input"
                  placeholder="Type a goal (e.g. 'find top HN posts') or /status, /usage…"
                  value={msg}
                  onChange={(event) => setMsg(event.target.value)}
                  disabled={send.isPending}
                />
                <button
                  type="submit"
                  className="btn primary"
                  disabled={send.isPending || !msg.trim()}
                >
                  Send
                </button>
              </form>
              <div style={{ fontSize: 11, color: 'var(--ink-400)' }}>
                Plain text launches a linked browser task. Slash commands execute inline and append
                their result to the same bounded session history.
              </div>

              <div className="inline" style={{ justifyContent: 'flex-end' }}>
                <button
                  type="button"
                  className="btn sm"
                  disabled={reset.isPending}
                  onClick={async () => {
                    if (!selected) return;
                    if (
                      !(await confirm({
                        title: 'Reset session history',
                        description: `Session "${selected.slice(0, 8)}" will lose its current message history.`,
                        confirmLabel: 'Reset history',
                        tone: 'danger',
                      }))
                    ) {
                      return;
                    }
                    reset.mutate(selected);
                  }}
                >
                  <Icon path={paths.refresh} size={12} />
                  Reset history
                </button>
                <button
                  type="button"
                  className="btn danger sm"
                  disabled={del.isPending}
                  onClick={async () => {
                    if (!selected) return;
                    if (
                      !(await confirm({
                        title: 'Delete session',
                        description: `Session "${selected.slice(0, 8)}" will be removed permanently. This cannot be undone.`,
                        confirmLabel: 'Delete session',
                        tone: 'danger',
                      }))
                    ) {
                      return;
                    }
                    del.mutate(selected);
                  }}
                >
                  <Icon path={paths.trash} size={12} />
                  Delete session
                </button>
              </div>
            </div>
          )}
        </Panel>
      </section>
    </div>
  );
}

function ConnectionPill({ state }: { state: 'connecting' | 'open' | 'closed' | 'error' }) {
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

function SessionRow({
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

function MetaTile({ label, value }: { label: string; value: string }) {
  return (
    <div className="meta-tile">
      <span className="meta-label">{label}</span>
      <span>{value}</span>
    </div>
  );
}

function SessionRunCard({
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
            <MetaTile label="Current URL" value={truncate(run.current_url || run.final_url || '—', 56)} />
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

function roleTone(role: string): 'user' | 'assistant' | 'system' {
  if (role === 'user') return 'user';
  if (role === 'assistant' || role === 'bot') return 'assistant';
  return 'system';
}

function MessageList({ messages }: { messages: SessionMessage[] }) {
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

function sortSessions(sessions: Session[], sort: SortKey): Session[] {
  return [...sessions].sort((left, right) => {
    if (sort === 'title') {
      return (left.title || left.id).localeCompare(right.title || right.id);
    }
    if (sort === 'created') {
      return right.created_at - left.created_at || right.last_active - left.last_active;
    }
    return right.last_active - left.last_active || right.created_at - left.created_at;
  });
}

function pickDefaultSessionId(sessions: Session[]): string | null {
  return sortSessions(sessions, 'activity')[0]?.id ?? null;
}

function extractLatestRun(messages: SessionMessage[]): SessionRunHint | null {
  for (let index = messages.length - 1; index >= 0; index -= 1) {
    const meta = messages[index]?.metadata || {};
    const runId = typeof meta.run_id === 'string' ? meta.run_id : null;
    if (!runId) continue;
    return {
      runId,
      status: typeof meta.status === 'string' ? meta.status : null,
      kind: typeof meta.kind === 'string' ? meta.kind : null,
    };
  }
  return null;
}

function readEventSessionId(event: DashboardEvent): string | null {
  const payload = event.payload;
  if (!payload || typeof payload !== 'object') return null;
  if ('session_id' in payload && payload.session_id != null) {
    return String(payload.session_id);
  }
  if (event.type === 'session.created' && 'id' in payload && payload.id != null) {
    return String(payload.id);
  }
  return null;
}

function badgeTone(status: string): 'ok' | 'warn' | 'err' | 'muted' {
  if (status === 'completed') return 'ok';
  if (status === 'running' || status === 'connecting') return 'warn';
  if (status === 'failed' || status === 'error') return 'err';
  return 'muted';
}

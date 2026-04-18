import { useDeferredValue, useEffect, useMemo, useState } from 'react';
import { Link, useSearchParams } from 'react-router-dom';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { api } from '../../lib/api';
import { useDashboardEvents } from '../../lib/sse';
import { useConfirm } from '../../components/ConfirmProvider';
import { EmptyState } from '../../components/EmptyState';
import { PageHeader } from '../../components/PageHeader';
import { Panel } from '../../components/Panel';
import { Skeleton } from '../../components/Skeleton';
import { useToasts } from '../../components/ToastProvider';
import { Icon, paths } from '../../lib/icons';
import { formatAbsolute, formatNumber, formatRelative, truncate } from '../../lib/format';
import {
  ConnectionPill,
  MessageList,
  MetaTile,
  SessionRow,
  SessionRunCard,
} from './components';
import {
  extractLatestRun,
  pickDefaultSessionId,
  readEventSessionId,
  sortSessions,
  type SortKey,
} from './helpers';

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

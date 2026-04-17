import { useDeferredValue, useMemo, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { api, type Session } from '../lib/api';
import { useConfirm } from '../components/ConfirmProvider';
import { EmptyState } from '../components/EmptyState';
import { PageHeader } from '../components/PageHeader';
import { Panel } from '../components/Panel';
import { Skeleton } from '../components/Skeleton';
import { useToasts } from '../components/ToastProvider';
import { Icon, paths } from '../lib/icons';
import { formatAbsolute, formatNumber, formatRelative } from '../lib/format';

type SortKey = 'activity' | 'title' | 'created';

export function Sessions() {
  const qc = useQueryClient();
  const confirm = useConfirm();
  const { push } = useToasts();
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
    refetchInterval: 5_000,
  });

  const sessions = list.data?.sessions ?? [];
  const selectedSession = useMemo(
    () => sessions.find((session) => session.id === selected) ?? null,
    [selected, sessions]
  );

  const filteredSessions = useMemo(() => {
    const needle = deferredSearch.trim().toLowerCase();
    return [...sessions]
      .filter((session) => {
        if (!needle) return true;
        return (
          session.id.toLowerCase().includes(needle) ||
          (session.title || '').toLowerCase().includes(needle)
        );
      })
      .sort((left, right) => {
        if (sort === 'title') {
          return (left.title || left.id).localeCompare(right.title || right.id);
        }
        if (sort === 'created') {
          return right.created_at - left.created_at || right.last_active - left.last_active;
        }
        return right.last_active - left.last_active || right.created_at - left.created_at;
      });
  }, [deferredSearch, sessions, sort]);

  const history = useQuery({
    queryKey: ['sessionHistory', selected],
    queryFn: () => (selected ? api.sessionHistory(selected, 200) : Promise.resolve(null)),
    enabled: !!selected,
    refetchInterval: selected ? 4_000 : false,
  });

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
      qc.invalidateQueries({ queryKey: ['sessionHistory', selected] });
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
      setSelected((current) => (current === id ? null : current));
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
    onSuccess: () => {
      setMsg('');
      qc.invalidateQueries({ queryKey: ['sessionHistory', selected] });
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
        description={`${formatNumber(sessions.length)} bounded histories for agent conversations and follow-up messages.`}
      />

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

          <Panel title="All sessions" tag={`${formatNumber(sessions.length)}`}>
            {list.isLoading && <Skeleton height={120} />}
            {list.data && sessions.length === 0 && (
              <EmptyState
                title="No sessions yet"
                description="Create one to begin streaming messages."
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
              description="Select a session on the left to inspect its history and send messages."
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

              <div className="stack-section">
                <div className="section-label">Messages</div>
                <div className="stack-list scroll">
                  {history.data.messages.length === 0 && (
                    <EmptyState
                      title="No messages"
                      description="Send the first message via the composer below."
                    />
                  )}
                  {history.data.messages.map((message, index) => (
                    <div key={index} className="meta-tile" style={{ gap: 8 }}>
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
                        <span>{message.role}</span>
                        <span>{formatRelative(message.ts)}</span>
                      </div>
                      <div className="mono" style={{ fontSize: 12, whiteSpace: 'pre-wrap' }}>
                        {message.content}
                      </div>
                    </div>
                  ))}
                </div>
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
                  placeholder="Send a message to the session…"
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

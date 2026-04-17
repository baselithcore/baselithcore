import { useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { api, type Session } from '../lib/api';
import { useConfirm } from '../components/ConfirmProvider';
import { PageHeader } from '../components/PageHeader';
import { Panel } from '../components/Panel';
import { EmptyState } from '../components/EmptyState';
import { Skeleton } from '../components/Skeleton';
import { useToasts } from '../components/ToastProvider';
import { Icon, paths } from '../lib/icons';
import { formatAbsolute, formatRelative } from '../lib/format';

export function Sessions() {
  const qc = useQueryClient();
  const confirm = useConfirm();
  const { push } = useToasts();
  const [title, setTitle] = useState('');
  const [primary, setPrimary] = useState(false);
  const [selected, setSelected] = useState<string | null>(null);
  const [msg, setMsg] = useState('');

  const list = useQuery({
    queryKey: ['sessions'],
    queryFn: api.sessions,
    refetchInterval: 5_000,
  });

  const history = useQuery({
    queryKey: ['sessionHistory', selected],
    queryFn: () => (selected ? api.sessionHistory(selected, 200) : Promise.resolve(null)),
    enabled: !!selected,
    refetchInterval: selected ? 4_000 : false,
  });

  const create = useMutation({
    mutationFn: () => api.createSession(title.trim(), primary),
    onSuccess: (s) => {
      setTitle('');
      setPrimary(false);
      setSelected(s.id);
      qc.invalidateQueries({ queryKey: ['sessions'] });
      push({
        tone: 'success',
        title: 'Session created',
        description: `${s.title || s.id} is ready for messages.`,
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
      setSelected(null);
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
        description="Create, inspect, reset, and delete agent sessions. Each session holds a bounded message history."
      />

      <section className="grid grid-split-1-2">
        <div style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>
          <Panel title="New session">
            <form
              style={{ display: 'flex', flexDirection: 'column', gap: 12 }}
              onSubmit={(e) => {
                e.preventDefault();
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
                  onChange={(e) => setTitle(e.target.value)}
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
                  onChange={(e) => setPrimary(e.target.checked)}
                />
                Mark as primary
              </label>
              <button type="submit" className="btn primary" disabled={create.isPending}>
                <Icon path={paths.plus} size={14} />
                Create session
              </button>
            </form>
          </Panel>

          <Panel title="All sessions" tag={`${list.data?.sessions.length ?? 0}`}>
            {list.isLoading && <Skeleton height={120} />}
            {list.data && list.data.sessions.length === 0 && (
              <EmptyState
                title="No sessions yet"
                description="Create one to begin streaming messages."
              />
            )}
            {list.data && list.data.sessions.length > 0 && (
              <div
                style={{
                  display: 'flex',
                  flexDirection: 'column',
                  gap: 6,
                  maxHeight: 480,
                  overflowY: 'auto',
                }}
              >
                {list.data.sessions.map((s) => (
                  <SessionRow
                    key={s.id}
                    session={s}
                    active={s.id === selected}
                    onSelect={() => setSelected(s.id)}
                  />
                ))}
              </div>
            )}
          </Panel>
        </div>

        <Panel
          title={selected ? `History · ${selected.slice(0, 8)}…` : 'History'}
          tag={selected && history.data ? `${history.data.messages.length} msgs` : ''}
        >
          {!selected && (
            <EmptyState
              title="Pick a session"
              description="Select a session on the left to inspect its history and send messages."
            />
          )}

          {selected && history.isLoading && <Skeleton height={240} />}

          {selected && history.data && (
            <div
              style={{
                display: 'flex',
                flexDirection: 'column',
                gap: 10,
              }}
            >
              <div
                style={{
                  maxHeight: 360,
                  overflowY: 'auto',
                  display: 'flex',
                  flexDirection: 'column',
                  gap: 6,
                  paddingRight: 4,
                }}
              >
                {history.data.messages.length === 0 && (
                  <EmptyState
                    title="No messages"
                    description="Send the first message via the composer below."
                  />
                )}
                {history.data.messages.map((m, i) => (
                  <div
                    key={i}
                    style={{
                      border: '1px solid var(--panel-border)',
                      borderRadius: 'var(--radius-md)',
                      background: 'rgba(15,19,25,0.55)',
                      padding: '10px 12px',
                    }}
                  >
                    <div
                      style={{
                        display: 'flex',
                        justifyContent: 'space-between',
                        fontSize: 11,
                        color: 'var(--ink-400)',
                        marginBottom: 4,
                      }}
                      className="mono"
                    >
                      <span>{m.role}</span>
                      <span>{formatRelative(m.ts)}</span>
                    </div>
                    <div className="mono" style={{ fontSize: 12, whiteSpace: 'pre-wrap' }}>
                      {m.content}
                    </div>
                  </div>
                ))}
              </div>

              <form
                className="inline"
                onSubmit={(e) => {
                  e.preventDefault();
                  if (!selected || !msg.trim() || send.isPending) return;
                  send.mutate({ id: selected, content: msg.trim() });
                }}
              >
                <input
                  className="input"
                  placeholder="Send a message to the session…"
                  value={msg}
                  onChange={(e) => setMsg(e.target.value)}
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
    <button
      type="button"
      onClick={onSelect}
      style={{
        cursor: 'pointer',
        textAlign: 'left',
        border: `1px solid ${active ? 'rgba(46,230,196,0.45)' : 'var(--panel-border)'}`,
        background: active ? 'rgba(46,230,196,0.08)' : 'rgba(15,19,25,0.55)',
        borderRadius: 'var(--radius-md)',
        padding: '10px 12px',
        display: 'flex',
        flexDirection: 'column',
        gap: 4,
      }}
    >
      <div
        style={{
          display: 'flex',
          justifyContent: 'space-between',
          alignItems: 'center',
          gap: 8,
        }}
      >
        <span className="mono" style={{ fontSize: 12, color: 'var(--ink-100)' }}>
          {session.title || session.id}
        </span>
        {session.primary && <span className="badge ok">primary</span>}
      </div>
      <div className="mono" style={{ fontSize: 10, color: 'var(--ink-400)' }}>
        {session.id} · {formatAbsolute(session.last_active)}
      </div>
    </button>
  );
}

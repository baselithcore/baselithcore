import { useMemo, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import {
  api,
  type AgentActionCatalogEntry,
  type AgentInfo,
  type CustomAgentPayload,
} from '../lib/api';
import { useConfirm } from '../components/ConfirmProvider';
import { PageHeader } from '../components/PageHeader';
import { Panel } from '../components/Panel';
import { EmptyState } from '../components/EmptyState';
import { Skeleton } from '../components/Skeleton';
import { useToasts } from '../components/ToastProvider';
import { Icon, paths } from '../lib/icons';
import { formatNumber, truncate } from '../lib/format';

export function Agents() {
  const qc = useQueryClient();
  const confirm = useConfirm();
  const { push } = useToasts();
  const [selected, setSelected] = useState<string | null>(null);
  const [formOpen, setFormOpen] = useState(false);
  const [dispatchQuery, setDispatchQuery] = useState('');
  const [dispatchResult, setDispatchResult] = useState<string | null>(null);

  const { data, isLoading } = useQuery({
    queryKey: ['agents'],
    queryFn: api.agents,
    refetchInterval: 15_000,
  });

  const { data: catalog } = useQuery({
    queryKey: ['agents', 'catalog'],
    queryFn: api.agentsCatalog,
    staleTime: 60_000,
  });

  const agents = useMemo(() => data?.agents ?? [], [data]);
  const active = useMemo(
    () => agents.find((agent) => agent.name === selected) ?? agents[0] ?? null,
    [agents, selected]
  );
  const totals = data?.totals ?? { all: 0, custom: 0, system: 0 };

  const invalidate = () => qc.invalidateQueries({ queryKey: ['agents'] });

  const create = useMutation({
    mutationFn: (payload: CustomAgentPayload) => api.createCustomAgent(payload),
    onSuccess: (_, payload) => {
      invalidate();
      setFormOpen(false);
      push({
        tone: 'success',
        title: 'Custom agent registered',
        description: `${payload.name} added to the registry.`,
      });
    },
    onError: (err: unknown) =>
      push({
        tone: 'error',
        title: 'Custom agent create failed',
        description: err instanceof Error ? err.message : String(err),
      }),
  });

  const remove = useMutation({
    mutationFn: (name: string) => api.deleteCustomAgent(name),
    onSuccess: (_, name) => {
      setSelected((cur) => (cur === name ? null : cur));
      invalidate();
      push({
        tone: 'success',
        title: 'Agent removed',
        description: `${name} was deleted from the registry.`,
      });
    },
    onError: (err: unknown) =>
      push({
        tone: 'error',
        title: 'Agent removal failed',
        description: err instanceof Error ? err.message : String(err),
      }),
  });

  const dispatch = useMutation({
    mutationFn: ({ name, query }: { name: string; query: string }) =>
      api.dispatchAgent(name, query),
    onSuccess: (res) => {
      setDispatchResult(JSON.stringify(res, null, 2));
      push({
        tone: 'success',
        title: 'Agent dispatched',
        description: `Result status: ${res.result && typeof (res.result as { status?: string }).status === 'string' ? (res.result as { status: string }).status : 'ok'}.`,
      });
    },
    onError: (err: unknown) =>
      push({
        tone: 'error',
        title: 'Agent dispatch failed',
        description: err instanceof Error ? err.message : String(err),
      }),
  });

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 24 }}>
      <PageHeader
        eyebrow="Routing"
        title="Registered agents"
        description={`${formatNumber(totals.all)} agents (${formatNumber(totals.system)} system · ${formatNumber(totals.custom)} custom). Inspect keywords, priority, metadata, and dispatch test queries.`}
      />

      <Panel>
        <div className="toolbar">
          <div className="muted" style={{ fontSize: 12 }}>
            Custom agents are persisted under the <span className="mono">custom.</span> prefix and
            survive restarts.
          </div>
          <button
            type="button"
            className="btn"
            style={{ marginLeft: 'auto' }}
            onClick={() => setFormOpen((open) => !open)}
          >
            {formOpen ? 'Close form' : 'New custom agent'}
          </button>
        </div>

        {formOpen && catalog && (
          <CustomAgentForm
            actions={catalog.actions}
            namePrefix={catalog.name_prefix}
            submitting={create.isPending}
            onSubmit={(payload) => create.mutate(payload)}
            onCancel={() => setFormOpen(false)}
          />
        )}
      </Panel>

      {isLoading && <Skeleton height={260} />}

      {!isLoading && agents.length === 0 && (
        <EmptyState
          title="No agents registered"
          description="System agents register at plugin boot. Use 'New custom agent' to add your own."
        />
      )}

      {agents.length > 0 && (
        <section className="grid grid-split-1-2">
          <Panel title="Roster" tag={`${agents.length}`}>
            <div className="stack-list">
              {agents.map((agent) => {
                const isActive = agent.name === active?.name;
                return (
                  <button
                    key={agent.name}
                    type="button"
                    className={`select-row ${isActive ? 'active' : ''}`}
                    onClick={() => {
                      setSelected(agent.name);
                      setDispatchResult(null);
                    }}
                  >
                    <div className="select-row-head">
                      <span className="mono" style={{ color: 'var(--ink-100)' }}>
                        {agent.name}
                      </span>
                      <span className={`badge ${agent.custom ? 'ok' : 'muted'}`}>
                        {agent.custom ? 'custom' : 'system'}
                      </span>
                      <span className="badge">p{agent.priority}</span>
                    </div>
                    <div className="muted" style={{ fontSize: 12 }}>
                      {truncate(agent.description || 'No description provided.', 110)}
                    </div>
                  </button>
                );
              })}
            </div>
          </Panel>

          <Panel
            title={active ? active.name : 'Agent details'}
            tag={active ? `${active.keywords.length} keywords` : ''}
          >
            {active ? (
              <div style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>
                <div className="detail-grid">
                  <MetaTile label="Kind" value={active.kind} />
                  <MetaTile label="Priority" value={String(active.priority)} />
                  <MetaTile label="Keywords" value={String(active.keywords.length)} />
                  <MetaTile
                    label="Metadata keys"
                    value={String(Object.keys(active.metadata).length)}
                  />
                </div>

                <section>
                  <div className="section-label">Description</div>
                  <div className="info-block">
                    {active.description || 'No description provided.'}
                  </div>
                </section>

                <section>
                  <div className="section-label">Keywords</div>
                  {active.keywords.length === 0 ? (
                    <div className="info-block muted">No keywords configured.</div>
                  ) : (
                    <div className="chip-row">
                      {active.keywords.map((keyword) => (
                        <span key={keyword} className="badge">
                          {keyword}
                        </span>
                      ))}
                    </div>
                  )}
                </section>

                <section>
                  <div className="section-label">Metadata</div>
                  <pre className="code-block">{JSON.stringify(active.metadata, null, 2)}</pre>
                </section>

                <section>
                  <div className="section-label">Dispatch test</div>
                  <div className="toolbar" style={{ gap: 8 }}>
                    <input
                      className="input toolbar-grow"
                      placeholder="Query to dispatch…"
                      value={dispatchQuery}
                      onChange={(event) => setDispatchQuery(event.target.value)}
                    />
                    <button
                      type="button"
                      className="btn"
                      disabled={dispatch.isPending || !dispatchQuery.trim()}
                      onClick={() =>
                        dispatch.mutate({ name: active.name, query: dispatchQuery.trim() })
                      }
                    >
                      {dispatch.isPending ? 'Dispatching…' : 'Run'}
                    </button>
                  </div>
                  {dispatchResult && (
                    <pre className="code-block" style={{ marginTop: 8 }}>
                      {dispatchResult}
                    </pre>
                  )}
                </section>

                {active.custom && (
                  <button
                    type="button"
                    className="btn danger"
                    disabled={remove.isPending}
                    onClick={async () => {
                      if (
                        !(await confirm({
                          title: 'Remove custom agent',
                          description: `"${active.name}" will be deleted from the registry.`,
                          confirmLabel: 'Remove agent',
                          tone: 'danger',
                        }))
                      ) {
                        return;
                      }
                      remove.mutate(active.name);
                    }}
                  >
                    <Icon path={paths.trash} size={14} />
                    Remove agent
                  </button>
                )}
              </div>
            ) : null}
          </Panel>
        </section>
      )}
    </div>
  );
}

interface CustomAgentFormProps {
  actions: AgentActionCatalogEntry[];
  namePrefix: string;
  submitting: boolean;
  onSubmit: (payload: CustomAgentPayload) => void;
  onCancel: () => void;
}

function CustomAgentForm({
  actions,
  namePrefix,
  submitting,
  onSubmit,
  onCancel,
}: CustomAgentFormProps) {
  const [name, setName] = useState('');
  const [description, setDescription] = useState('');
  const [keywordsRaw, setKeywordsRaw] = useState('');
  const [priority, setPriority] = useState('100');
  const [actionType, setActionType] = useState(actions[0]?.type ?? 'chat_command');
  const [slashCommand, setSlashCommand] = useState('/status');
  const [webhookUrl, setWebhookUrl] = useState('https://');
  const [webhookHeaders, setWebhookHeaders] = useState('{}');
  const [webhookTimeout, setWebhookTimeout] = useState('15');
  const [staticPayload, setStaticPayload] = useState('{"ok": true}');
  const [localError, setLocalError] = useState<string | null>(null);

  const handleSubmit = () => {
    setLocalError(null);
    const trimmedName = name.trim();
    if (!trimmedName) {
      setLocalError('Name is required.');
      return;
    }
    const pri = Number(priority);
    if (!Number.isFinite(pri) || pri < 0 || pri > 10_000) {
      setLocalError('Priority must be between 0 and 10000.');
      return;
    }
    const keywords = keywordsRaw
      .split(',')
      .map((kw) => kw.trim())
      .filter(Boolean);

    let params: Record<string, unknown>;
    try {
      if (actionType === 'chat_command') {
        if (!slashCommand.startsWith('/')) {
          throw new Error("Command must start with '/'.");
        }
        params = { command: slashCommand };
      } else if (actionType === 'http_webhook') {
        if (!webhookUrl.startsWith('http')) {
          throw new Error('URL must start with http:// or https://');
        }
        const headers = webhookHeaders.trim() ? JSON.parse(webhookHeaders) : {};
        const timeoutN = Number(webhookTimeout);
        if (!Number.isFinite(timeoutN) || timeoutN < 1 || timeoutN > 60) {
          throw new Error('Timeout must be between 1 and 60 seconds.');
        }
        params = { url: webhookUrl, headers, timeout_seconds: timeoutN };
      } else if (actionType === 'static_response') {
        const payload = staticPayload.trim() ? JSON.parse(staticPayload) : {};
        if (typeof payload !== 'object' || Array.isArray(payload) || payload === null) {
          throw new Error('Static payload must be a JSON object.');
        }
        params = { payload };
      } else {
        throw new Error(`Unsupported action '${actionType}'.`);
      }
    } catch (err) {
      setLocalError(err instanceof Error ? err.message : String(err));
      return;
    }

    onSubmit({
      name: trimmedName,
      description: description.trim(),
      keywords,
      priority: pri,
      metadata: {},
      action: { type: actionType, params },
    });
  };

  return (
    <div className="stack-section" style={{ marginTop: 12 }}>
      <div className="section-label">New custom agent</div>
      <div style={{ display: 'grid', gap: 10 }}>
        <label className="field">
          <span>Name (prefix "{namePrefix}" auto-applied if missing)</span>
          <input
            className="input"
            placeholder="my-agent"
            value={name}
            onChange={(event) => setName(event.target.value)}
          />
        </label>

        <label className="field">
          <span>Description (optional)</span>
          <input
            className="input"
            value={description}
            onChange={(event) => setDescription(event.target.value)}
          />
        </label>

        <label className="field">
          <span>Keywords (comma-separated)</span>
          <input
            className="input"
            placeholder="python, bug, code"
            value={keywordsRaw}
            onChange={(event) => setKeywordsRaw(event.target.value)}
          />
        </label>

        <label className="field">
          <span>Priority (0–10000; higher wins on tied keyword hits)</span>
          <input
            className="input"
            type="number"
            min={0}
            max={10_000}
            step={1}
            value={priority}
            onChange={(event) => setPriority(event.target.value)}
          />
        </label>

        <label className="field">
          <span>Action</span>
          <select
            className="select"
            value={actionType}
            onChange={(event) => setActionType(event.target.value)}
          >
            {actions.map((entry) => (
              <option key={entry.type} value={entry.type}>
                {entry.label} — {entry.type}
              </option>
            ))}
          </select>
        </label>

        {actionType === 'chat_command' && (
          <label className="field">
            <span>Slash command (must start with /)</span>
            <input
              className="input"
              value={slashCommand}
              onChange={(event) => setSlashCommand(event.target.value)}
              placeholder="/status"
            />
          </label>
        )}

        {actionType === 'http_webhook' && (
          <>
            <label className="field">
              <span>URL</span>
              <input
                className="input"
                value={webhookUrl}
                onChange={(event) => setWebhookUrl(event.target.value)}
                placeholder="https://example.com/agent"
              />
            </label>
            <label className="field">
              <span>Headers (JSON)</span>
              <textarea
                className="input"
                rows={2}
                value={webhookHeaders}
                onChange={(event) => setWebhookHeaders(event.target.value)}
              />
            </label>
            <label className="field">
              <span>Timeout (seconds, 1–60)</span>
              <input
                className="input"
                type="number"
                min={1}
                max={60}
                step={1}
                value={webhookTimeout}
                onChange={(event) => setWebhookTimeout(event.target.value)}
              />
            </label>
          </>
        )}

        {actionType === 'static_response' && (
          <label className="field">
            <span>Static payload (JSON object)</span>
            <textarea
              className="input"
              rows={4}
              value={staticPayload}
              onChange={(event) => setStaticPayload(event.target.value)}
            />
          </label>
        )}

        {localError && (
          <div className="info-block" style={{ color: 'var(--danger, crimson)' }}>
            {localError}
          </div>
        )}

        <div className="toolbar" style={{ gap: 8 }}>
          <button type="button" className="btn" disabled={submitting} onClick={handleSubmit}>
            {submitting ? 'Creating…' : 'Create agent'}
          </button>
          <button type="button" className="btn" disabled={submitting} onClick={onCancel}>
            Cancel
          </button>
        </div>
      </div>
    </div>
  );
}

function MetaTile({ label, value }: { label: string; value: string }) {
  return (
    <div className="meta-tile">
      <span className="meta-label">{label}</span>
      <span className="mono" style={{ color: 'var(--ink-100)' }}>
        {value}
      </span>
    </div>
  );
}

export type { AgentInfo };

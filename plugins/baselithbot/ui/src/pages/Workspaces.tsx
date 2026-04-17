import { useMemo, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import {
  api,
  type WorkspaceCreatePayload,
  type WorkspaceInfo,
  type WorkspaceUpdatePayload,
} from '../lib/api';
import { useConfirm } from '../components/ConfirmProvider';
import { PageHeader } from '../components/PageHeader';
import { Panel } from '../components/Panel';
import { EmptyState } from '../components/EmptyState';
import { Skeleton } from '../components/Skeleton';
import { useToasts } from '../components/ToastProvider';
import { Icon, paths } from '../lib/icons';
import { formatAbsolute, formatNumber, truncate } from '../lib/format';

export function Workspaces() {
  const qc = useQueryClient();
  const confirm = useConfirm();
  const { push } = useToasts();
  const [selected, setSelected] = useState<string | null>(null);
  const [formMode, setFormMode] = useState<'closed' | 'create' | 'edit'>('closed');

  const { data, isLoading } = useQuery({
    queryKey: ['workspaces'],
    queryFn: api.workspaces,
    refetchInterval: 15_000,
  });

  const workspaces = useMemo(() => data?.workspaces ?? [], [data]);
  const active = useMemo(
    () => workspaces.find((w) => w.name === selected) ?? workspaces[0] ?? null,
    [workspaces, selected]
  );
  const totalOverrides = workspaces.reduce((sum, ws) => sum + ws.channels_overridden.length, 0);

  const invalidate = () => qc.invalidateQueries({ queryKey: ['workspaces'] });

  const create = useMutation({
    mutationFn: (payload: WorkspaceCreatePayload) => api.createWorkspace(payload),
    onSuccess: (res) => {
      invalidate();
      setFormMode('closed');
      setSelected(res.workspace.name);
      push({
        tone: 'success',
        title: 'Workspace created',
        description: `${res.workspace.name} added.`,
      });
    },
    onError: (err: unknown) =>
      push({
        tone: 'error',
        title: 'Workspace create failed',
        description: err instanceof Error ? err.message : String(err),
      }),
  });

  const update = useMutation({
    mutationFn: ({ name, payload }: { name: string; payload: WorkspaceUpdatePayload }) =>
      api.updateWorkspace(name, payload),
    onSuccess: (res) => {
      invalidate();
      setFormMode('closed');
      push({
        tone: 'success',
        title: 'Workspace updated',
        description: `${res.workspace.name} saved.`,
      });
    },
    onError: (err: unknown) =>
      push({
        tone: 'error',
        title: 'Workspace update failed',
        description: err instanceof Error ? err.message : String(err),
      }),
  });

  const remove = useMutation({
    mutationFn: (name: string) => api.deleteWorkspace(name),
    onSuccess: (_, name) => {
      setSelected((cur) => (cur === name ? null : cur));
      invalidate();
      push({
        tone: 'success',
        title: 'Workspace removed',
        description: `${name} was deleted.`,
      });
    },
    onError: (err: unknown) =>
      push({
        tone: 'error',
        title: 'Workspace removal failed',
        description: err instanceof Error ? err.message : String(err),
      }),
  });

  const primaryCount = workspaces.filter((w) => w.primary).length;
  const canDelete = active !== null && !active.primary && workspaces.length > 1;

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 24 }}>
      <PageHeader
        eyebrow="Isolation"
        title="Workspaces"
        description={`${formatNumber(workspaces.length)} workspaces · ${formatNumber(primaryCount)} primary · ${formatNumber(totalOverrides)} channel overrides bound to runtime state.`}
      />

      <Panel>
        <div className="toolbar">
          <div className="muted" style={{ fontSize: 12 }}>
            Each workspace has isolated channels, sessions, and skills. The{' '}
            <span className="mono">primary</span> workspace receives default traffic.
          </div>
          <button
            type="button"
            className="btn"
            style={{ marginLeft: 'auto' }}
            onClick={() => setFormMode(formMode === 'create' ? 'closed' : 'create')}
          >
            {formMode === 'create' ? 'Close form' : 'New workspace'}
          </button>
        </div>

        {formMode === 'create' && (
          <WorkspaceForm
            mode="create"
            submitting={create.isPending}
            onSubmit={(payload) => create.mutate(payload)}
            onCancel={() => setFormMode('closed')}
          />
        )}

        {formMode === 'edit' && active && (
          <WorkspaceForm
            mode="edit"
            existing={active}
            submitting={update.isPending}
            onSubmit={(payload) => update.mutate({ name: active.name, payload })}
            onCancel={() => setFormMode('closed')}
          />
        )}
      </Panel>

      {isLoading && <Skeleton height={260} />}

      {!isLoading && workspaces.length === 0 && (
        <EmptyState
          title="No workspaces configured"
          description="Click 'New workspace' to provision an isolated state bucket."
        />
      )}

      {workspaces.length > 0 && (
        <section className="grid grid-split-1-2">
          <Panel title="Roster" tag={`${workspaces.length}`}>
            <div className="stack-list">
              {workspaces.map((ws) => {
                const isActive = ws.name === active?.name;
                return (
                  <button
                    key={ws.name}
                    type="button"
                    className={`select-row ${isActive ? 'active' : ''}`}
                    onClick={() => {
                      setSelected(ws.name);
                      setFormMode('closed');
                    }}
                  >
                    <div className="select-row-head">
                      <span className="mono" style={{ color: 'var(--ink-100)' }}>
                        {ws.name}
                      </span>
                      <span className={`badge ${ws.primary ? 'ok' : 'muted'}`}>
                        {ws.primary ? 'primary' : 'isolated'}
                      </span>
                      <span className="badge">{ws.channels_overridden.length} ovr</span>
                    </div>
                    <div className="muted" style={{ fontSize: 12 }}>
                      {truncate(ws.description || 'No description provided.', 110)}
                    </div>
                  </button>
                );
              })}
            </div>
          </Panel>

          <Panel
            title={active ? active.name : 'Workspace details'}
            tag={active ? (active.primary ? 'primary' : 'isolated') : ''}
          >
            {active ? (
              <div style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>
                <div className="detail-grid">
                  <MetaTile label="Created" value={formatAbsolute(active.created_at)} />
                  <MetaTile label="Overrides" value={String(active.channels_overridden.length)} />
                  <MetaTile
                    label="Metadata keys"
                    value={String(Object.keys(active.metadata ?? {}).length)}
                  />
                  <MetaTile label="Role" value={active.primary ? 'primary' : 'isolated'} />
                </div>

                <section>
                  <div className="section-label">Description</div>
                  <div className="info-block">
                    {active.description || 'No description provided.'}
                  </div>
                </section>

                <section>
                  <div className="section-label">Runtime flags</div>
                  <div className="chip-row">
                    <span className={`badge ${active.primary ? 'ok' : 'muted'}`}>
                      {active.primary ? 'primary' : 'isolated'}
                    </span>
                    <span className="badge">
                      {active.channels_overridden.length > 0
                        ? 'channel overrides'
                        : 'default channels'}
                    </span>
                  </div>
                </section>

                <section>
                  <div className="section-label">Channel overrides</div>
                  {active.channels_overridden.length === 0 ? (
                    <div className="info-block muted">
                      No channel-specific overrides configured.
                    </div>
                  ) : (
                    <div className="chip-row">
                      {active.channels_overridden.map((channel) => (
                        <span key={channel} className="badge">
                          {channel}
                        </span>
                      ))}
                    </div>
                  )}
                </section>

                <section>
                  <div className="section-label">Metadata</div>
                  <pre className="code-block">{JSON.stringify(active.metadata ?? {}, null, 2)}</pre>
                </section>

                <div className="toolbar" style={{ gap: 8 }}>
                  <button
                    type="button"
                    className="btn"
                    onClick={() => setFormMode(formMode === 'edit' ? 'closed' : 'edit')}
                  >
                    <Icon path={paths.bolt} size={14} />
                    {formMode === 'edit' ? 'Close editor' : 'Edit workspace'}
                  </button>
                  <button
                    type="button"
                    className="btn danger"
                    disabled={!canDelete || remove.isPending}
                    title={
                      !canDelete
                        ? active.primary
                          ? 'Promote another workspace before removing the primary one'
                          : 'Cannot remove the last workspace'
                        : undefined
                    }
                    onClick={async () => {
                      if (
                        !(await confirm({
                          title: 'Remove workspace',
                          description: `"${active.name}" will be deleted along with its isolated state.`,
                          confirmLabel: 'Remove workspace',
                          tone: 'danger',
                        }))
                      ) {
                        return;
                      }
                      remove.mutate(active.name);
                    }}
                  >
                    <Icon path={paths.trash} size={14} />
                    Remove workspace
                  </button>
                </div>
              </div>
            ) : null}
          </Panel>
        </section>
      )}
    </div>
  );
}

type WorkspaceFormProps =
  | {
      mode: 'create';
      existing?: undefined;
      submitting: boolean;
      onSubmit: (payload: WorkspaceCreatePayload) => void;
      onCancel: () => void;
    }
  | {
      mode: 'edit';
      existing: WorkspaceInfo;
      submitting: boolean;
      onSubmit: (payload: WorkspaceUpdatePayload) => void;
      onCancel: () => void;
    };

function WorkspaceForm({ mode, existing, submitting, onSubmit, onCancel }: WorkspaceFormProps) {
  const [name, setName] = useState(existing?.name ?? '');
  const [description, setDescription] = useState(existing?.description ?? '');
  const [primary, setPrimary] = useState(existing?.primary ?? false);
  const [metadataRaw, setMetadataRaw] = useState(
    existing ? JSON.stringify(existing.metadata ?? {}, null, 2) : '{}'
  );
  const [overridesRaw, setOverridesRaw] = useState(
    existing
      ? JSON.stringify(
          existing.channels_overridden.reduce<Record<string, Record<string, unknown>>>(
            (acc, channel) => {
              acc[channel] = {};
              return acc;
            },
            {}
          ),
          null,
          2
        )
      : '{}'
  );
  const [localError, setLocalError] = useState<string | null>(null);

  const handleSubmit = () => {
    setLocalError(null);
    const trimmed = name.trim();
    if (mode === 'create' && !trimmed) {
      setLocalError('Name is required.');
      return;
    }
    let metadata: Record<string, unknown> = {};
    let overrides: Record<string, Record<string, unknown>> = {};
    try {
      metadata = metadataRaw.trim() ? JSON.parse(metadataRaw) : {};
      if (typeof metadata !== 'object' || Array.isArray(metadata) || metadata === null) {
        throw new Error('metadata must be a JSON object');
      }
    } catch (err) {
      setLocalError(`Invalid metadata JSON: ${err instanceof Error ? err.message : err}`);
      return;
    }
    try {
      overrides = overridesRaw.trim() ? JSON.parse(overridesRaw) : {};
      if (typeof overrides !== 'object' || Array.isArray(overrides) || overrides === null) {
        throw new Error('channel_overrides must be a JSON object');
      }
    } catch (err) {
      setLocalError(`Invalid overrides JSON: ${err instanceof Error ? err.message : err}`);
      return;
    }
    if (mode === 'create') {
      onSubmit({
        name: trimmed,
        description: description.trim(),
        primary,
        channel_overrides: overrides,
        metadata,
      });
    } else {
      onSubmit({
        description: description.trim(),
        primary,
        channel_overrides: overrides,
        metadata,
      });
    }
  };

  return (
    <div className="stack-section" style={{ marginTop: 12 }}>
      <div className="section-label">
        {mode === 'create' ? 'New workspace' : `Edit ${existing?.name ?? ''}`}
      </div>
      <div style={{ display: 'grid', gap: 10 }}>
        <label className="field">
          <span>Name</span>
          <input
            className="input"
            value={name}
            disabled={mode === 'edit'}
            onChange={(event) => setName(event.target.value)}
            placeholder="sandbox"
          />
        </label>

        <label className="field">
          <span>Description (optional)</span>
          <input
            className="input"
            value={description}
            onChange={(event) => setDescription(event.target.value)}
            placeholder="Isolated workspace for experiments"
          />
        </label>

        <label className="field">
          <span>
            <input
              type="checkbox"
              checked={primary}
              onChange={(event) => setPrimary(event.target.checked)}
              style={{ marginRight: 8 }}
            />
            Primary (demotes current primary when enabled)
          </span>
        </label>

        <label className="field">
          <span>Channel overrides (JSON object)</span>
          <textarea
            className="input"
            rows={4}
            value={overridesRaw}
            onChange={(event) => setOverridesRaw(event.target.value)}
            placeholder='{"slack": {"workspace_id": "T123"}}'
          />
        </label>

        <label className="field">
          <span>Metadata (JSON object)</span>
          <textarea
            className="input"
            rows={4}
            value={metadataRaw}
            onChange={(event) => setMetadataRaw(event.target.value)}
            placeholder='{"team": "ops"}'
          />
        </label>

        {localError && (
          <div className="info-block" style={{ color: 'var(--danger, crimson)' }}>
            {localError}
          </div>
        )}

        <div className="toolbar" style={{ gap: 8 }}>
          <button type="button" className="btn" disabled={submitting} onClick={handleSubmit}>
            {submitting ? 'Saving…' : mode === 'create' ? 'Create workspace' : 'Save changes'}
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

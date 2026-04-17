import { useDeferredValue, useMemo, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { ApiError, api, type Skill } from '../lib/api';
import { useConfirm } from '../components/ConfirmProvider';
import { DetailDrawer } from '../components/DetailDrawer';
import { EmptyState } from '../components/EmptyState';
import { PageHeader } from '../components/PageHeader';
import { Panel } from '../components/Panel';
import { Skeleton } from '../components/Skeleton';
import { useToasts } from '../components/ToastProvider';
import { formatNumber } from '../lib/format';

const SCOPES = ['', 'bundled', 'managed', 'workspace'] as const;

type SortKey = 'name' | 'scope' | 'version';
type CatalogSkill = {
  name: string;
  version: string;
  description: string;
  entrypoint: string | null;
};

function toErrorMessage(error: unknown): string {
  if (error instanceof ApiError) return `${error.status}: ${error.message}`;
  if (error instanceof Error) return error.message;
  return 'Unknown error';
}

function normalizeCatalogEntry(entry: Record<string, unknown>): CatalogSkill | null {
  const name = typeof entry.name === 'string' ? entry.name.trim() : '';
  if (!name) return null;
  return {
    name,
    version: typeof entry.version === 'string' && entry.version.trim() ? entry.version : '0.0.0',
    description: typeof entry.description === 'string' ? entry.description : '',
    entrypoint: typeof entry.entrypoint === 'string' ? entry.entrypoint : null,
  };
}

export function Skills() {
  const queryClient = useQueryClient();
  const { push } = useToasts();
  const confirm = useConfirm();
  const [scope, setScope] = useState<(typeof SCOPES)[number]>('');
  const [search, setSearch] = useState('');
  const [sort, setSort] = useState<SortKey>('name');
  const [selectedName, setSelectedName] = useState<string | null>(null);
  const [installName, setInstallName] = useState('');
  const deferredSearch = useDeferredValue(search);

  const { data, isLoading } = useQuery({
    queryKey: ['skills', scope],
    queryFn: () => api.skills(scope || undefined),
    refetchInterval: 15_000,
  });

  const clawhubQuery = useQuery({
    queryKey: ['skills', 'clawhub'],
    queryFn: () => api.clawhubStatus(),
    refetchInterval: 60_000,
  });

  const catalogQuery = useQuery({
    queryKey: ['skills', 'clawhubCatalog'],
    queryFn: () => api.clawhubCatalog(),
    refetchInterval: 60_000,
  });

  const invalidateSkills = () => {
    queryClient.invalidateQueries({ queryKey: ['skills'] });
  };

  const syncMutation = useMutation({
    mutationFn: () => api.clawhubSync(),
    onSuccess: (result) => {
      push({
        title: 'ClawHub sync complete',
        description: `${result.installed} skill(s) installed.`,
        tone: 'success',
      });
      invalidateSkills();
    },
    onError: (error) =>
      push({ title: 'Sync failed', description: toErrorMessage(error), tone: 'error' }),
  });

  const installMutation = useMutation({
    mutationFn: (name: string) => api.clawhubInstall(name),
    onSuccess: (result) => {
      push({
        title: `Installed "${result.name}"`,
        description: `${formatNumber(result.bytes)} bytes written.`,
        tone: 'success',
      });
      setInstallName('');
      invalidateSkills();
    },
    onError: (error) =>
      push({ title: 'Install failed', description: toErrorMessage(error), tone: 'error' }),
  });

  const rescanMutation = useMutation({
    mutationFn: () => api.rescanSkills(),
    onSuccess: (result) => {
      push({
        title: 'Workspace rescanned',
        description: `Removed ${result.removed}, registered ${result.workspace_skills.length}.`,
        tone: 'success',
      });
      invalidateSkills();
    },
    onError: (error) =>
      push({ title: 'Rescan failed', description: toErrorMessage(error), tone: 'error' }),
  });

  const removeMutation = useMutation({
    mutationFn: (name: string) => api.removeSkill(name),
    onSuccess: (result) => {
      push({
        title: `Removed "${result.name}"`,
        description: `Scope: ${result.scope}.`,
        tone: 'success',
      });
      if (selectedName === result.name) setSelectedName(null);
      invalidateSkills();
    },
    onError: (error) =>
      push({ title: 'Remove failed', description: toErrorMessage(error), tone: 'error' }),
  });

  const requestRemove = async (skill: Skill) => {
    const ok = await confirm({
      title: `Remove skill "${skill.name}"?`,
      description: `Scope: ${skill.scope}. This cannot be undone.`,
      confirmLabel: 'Remove',
      cancelLabel: 'Cancel',
      tone: 'danger',
    });
    if (ok) removeMutation.mutate(skill.name);
  };

  const skills = data?.skills ?? [];
  const installedNames = useMemo(() => new Set(skills.map((skill) => skill.name)), [skills]);
  const selected = useMemo(
    () => skills.find((skill) => skill.name === selectedName) ?? null,
    [selectedName, skills]
  );
  const catalog = useMemo(
    () =>
      (catalogQuery.data?.entries ?? [])
        .map((entry) => normalizeCatalogEntry(entry))
        .filter((entry): entry is CatalogSkill => entry !== null)
        .sort((left, right) => left.name.localeCompare(right.name)),
    [catalogQuery.data]
  );

  const totals = useMemo(() => {
    const counts: Record<string, number> = { bundled: 0, managed: 0, workspace: 0 };
    for (const s of skills) counts[s.scope] = (counts[s.scope] ?? 0) + 1;
    return counts;
  }, [skills]);

  const filtered = useMemo(() => {
    const needle = deferredSearch.trim().toLowerCase();
    return skills
      .filter((skill) => {
        if (!needle) return true;
        return (
          skill.name.toLowerCase().includes(needle) ||
          skill.scope.toLowerCase().includes(needle) ||
          skill.version.toLowerCase().includes(needle) ||
          (skill.description || '').toLowerCase().includes(needle)
        );
      })
      .sort((left, right) => {
        if (sort === 'scope') {
          return left.scope.localeCompare(right.scope) || left.name.localeCompare(right.name);
        }
        if (sort === 'version') {
          return right.version.localeCompare(left.version) || left.name.localeCompare(right.name);
        }
        return left.name.localeCompare(right.name);
      });
  }, [deferredSearch, skills, sort]);

  if (isLoading || !data) return <Skeleton height={280} />;

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 24 }}>
      <PageHeader
        eyebrow="Capabilities"
        title="Skills registry"
        description={`${formatNumber(skills.length)} skills — ${totals.bundled} bundled, ${totals.managed} managed, ${totals.workspace} workspace.`}
      />

      <Panel>
        <div className="stack-section">
          <div className="section-label">ClawHub registry</div>
          {clawhubQuery.data && (
            <div className="detail-grid">
              <MetaTile label="Base URL" value={clawhubQuery.data.base_url} />
              <MetaTile label="Install dir" value={clawhubQuery.data.install_dir} />
              <MetaTile
                label="Auth"
                value={clawhubQuery.data.auth_token_set ? 'configured' : 'anonymous'}
              />
            </div>
          )}
          <div className="toolbar" style={{ marginTop: 12 }}>
            <input
              className="input toolbar-grow"
              placeholder="Install by name (e.g. my-skill)"
              value={installName}
              onChange={(event) => setInstallName(event.target.value)}
            />
            <button
              type="button"
              className="btn primary"
              disabled={!installName.trim() || installMutation.isPending}
              onClick={() => installMutation.mutate(installName.trim())}
            >
              {installMutation.isPending ? 'Installing…' : 'Install from ClawHub'}
            </button>
            <button
              type="button"
              className="btn"
              disabled={syncMutation.isPending}
              onClick={() => syncMutation.mutate()}
            >
              {syncMutation.isPending ? 'Syncing…' : 'Sync catalog'}
            </button>
            <button
              type="button"
              className="btn"
              disabled={rescanMutation.isPending}
              onClick={() => rescanMutation.mutate()}
            >
              {rescanMutation.isPending ? 'Rescanning…' : 'Rescan workspace'}
            </button>
          </div>

          {catalogQuery.error && (
            <div className="info-block" style={{ marginTop: 12 }}>
              Catalog unavailable: {toErrorMessage(catalogQuery.error)}
            </div>
          )}

          {!catalogQuery.error && catalogQuery.isLoading && <Skeleton height={160} />}

          {!catalogQuery.error && !catalogQuery.isLoading && (
            <>
              {catalog.length === 0 ? (
                <div className="info-block" style={{ marginTop: 12 }}>
                  No installable skills were returned by the configured ClawHub registry.
                </div>
              ) : (
                <div className="cards-grid" style={{ marginTop: 12 }}>
                  {catalog.map((entry) => {
                    const installed = installedNames.has(entry.name);
                    return (
                      <div key={entry.name} className="record-card">
                        <div className="record-card-head">
                          <div className="record-card-title mono">{entry.name}</div>
                          <span className="badge">{installed ? 'installed' : 'catalog'}</span>
                        </div>
                        <div className="record-card-meta">
                          <div className="record-kv">
                            <span>Version</span>
                            <span className="mono">{entry.version}</span>
                          </div>
                          <div className="record-kv">
                            <span>Entrypoint</span>
                            <span className="mono">{entry.entrypoint || '—'}</span>
                          </div>
                        </div>
                        <div className="info-block">
                          {entry.description || 'No description provided for this catalog entry.'}
                        </div>
                        <div className="toolbar" style={{ marginTop: 12 }}>
                          <button
                            type="button"
                            className="btn primary"
                            disabled={installed || installMutation.isPending}
                            onClick={() => installMutation.mutate(entry.name)}
                          >
                            {installed
                              ? 'Installed'
                              : installMutation.isPending
                                ? 'Installing…'
                                : 'Install'}
                          </button>
                        </div>
                      </div>
                    );
                  })}
                </div>
              )}
            </>
          )}
        </div>
      </Panel>

      <Panel>
        <div className="toolbar">
          <input
            className="input toolbar-grow"
            placeholder="Filter skills…"
            value={search}
            onChange={(event) => setSearch(event.target.value)}
          />
          <select
            className="select"
            value={scope}
            onChange={(event) => setScope(event.target.value as (typeof SCOPES)[number])}
          >
            <option value="">all scopes</option>
            <option value="bundled">bundled</option>
            <option value="managed">managed</option>
            <option value="workspace">workspace</option>
          </select>
          <select
            className="select"
            value={sort}
            onChange={(event) => setSort(event.target.value as SortKey)}
          >
            <option value="name">sort: name</option>
            <option value="scope">sort: scope</option>
            <option value="version">sort: version</option>
          </select>
        </div>

        {filtered.length === 0 ? (
          <EmptyState
            title={skills.length === 0 ? 'No skills installed' : 'No skills match'}
            description={
              skills.length === 0
                ? 'Register skills via the SkillRegistry, sync ClawHub, or rescan workspace.'
                : 'Try another search term or switch to a broader scope.'
            }
          />
        ) : (
          <div className="cards-grid">
            {filtered.map((skill) => (
              <button
                key={skill.name}
                type="button"
                className="record-card"
                onClick={() => setSelectedName(skill.name)}
              >
                <div className="record-card-head">
                  <div className="record-card-title mono">{skill.name}</div>
                  <span className="badge">{skill.scope}</span>
                </div>
                <div className="record-card-meta">
                  <div className="record-kv">
                    <span>Version</span>
                    <span className="mono">{skill.version}</span>
                  </div>
                  <div className="record-kv">
                    <span>Entrypoint</span>
                    <span className="mono">{skill.entrypoint || '—'}</span>
                  </div>
                </div>
                <div className="info-block">
                  {skill.description || 'No description provided for this skill entry.'}
                </div>
              </button>
            ))}
          </div>
        )}
      </Panel>

      <DetailDrawer
        open={!!selected}
        title={selected?.name ?? ''}
        subtitle="Skill details"
        onClose={() => setSelectedName(null)}
      >
        {selected && (
          <>
            <div className="detail-grid">
              <MetaTile label="Scope" value={selected.scope} />
              <MetaTile label="Version" value={selected.version} />
              <MetaTile label="Entrypoint" value={selected.entrypoint || '—'} />
            </div>

            <div className="stack-section">
              <div className="section-label">Description</div>
              <div className="info-block">
                {selected.description || 'No description provided for this skill entry.'}
              </div>
            </div>

            <div className="stack-section">
              <div className="section-label">Metadata</div>
              <pre className="code-block">{JSON.stringify(selected.metadata ?? {}, null, 2)}</pre>
            </div>

            {selected.scope !== 'bundled' && (
              <div className="stack-section">
                <button
                  type="button"
                  className="btn danger"
                  disabled={removeMutation.isPending}
                  onClick={() => requestRemove(selected)}
                >
                  {removeMutation.isPending ? 'Removing…' : 'Remove skill'}
                </button>
              </div>
            )}
          </>
        )}
      </DetailDrawer>
    </div>
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

import { useDeferredValue, useMemo, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { ApiError, api, type Skill, type WorkspaceSkillReport } from '../lib/api';
import { useConfirm } from '../components/ConfirmProvider';
import { DetailDrawer } from '../components/DetailDrawer';
import { EmptyState } from '../components/EmptyState';
import { PageHeader } from '../components/PageHeader';
import { Panel } from '../components/Panel';
import { Skeleton } from '../components/Skeleton';
import { StatCard } from '../components/StatCard';
import { useToasts } from '../components/ToastProvider';
import { formatNumber } from '../lib/format';
import { Icon, paths } from '../lib/icons';

const SCOPES = ['', 'bundled', 'managed', 'workspace'] as const;
const SCOPE_ORDER = ['bundled', 'managed', 'workspace'] as const;

type ScopeName = (typeof SCOPE_ORDER)[number];
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

function scopeTone(scope: ScopeName): 'ok' | 'warn' | 'muted' {
  if (scope === 'managed') return 'ok';
  if (scope === 'workspace') return 'warn';
  return 'muted';
}

function scopeLabel(scope: ScopeName): string {
  return scope.charAt(0).toUpperCase() + scope.slice(1);
}

function readSourceCount(skill: Skill): number {
  const sources = skill.metadata?.sources;
  if (!sources || typeof sources !== 'object' || Array.isArray(sources)) return 0;
  return Object.keys(sources).length;
}

function skillSummary(skill: Skill): string {
  const kind = typeof skill.metadata?.kind === 'string' ? skill.metadata.kind : '';
  if (skill.scope === 'bundled') return 'Native Baselithbot capability';
  if (skill.scope === 'managed') {
    return skill.metadata?.source === 'clawhub'
      ? 'Installed from ClawHub'
      : 'Managed registry skill';
  }
  if (kind === 'custom_skill') return 'Custom local skill bundle';
  const sourceCount = readSourceCount(skill);
  if (sourceCount > 0) {
    return `${sourceCount} prompt file${sourceCount === 1 ? '' : 's'} loaded from workspace`;
  }
  return 'Workspace prompt bundle';
}

function skillMetaBadges(skill: Skill): string[] {
  const badges = [`v${skill.version}`];
  const validation = skill.metadata?.validation;
  const validationStatus =
    validation && typeof validation === 'object' && 'status' in validation
      ? String(validation.status)
      : '';
  if (skill.scope === 'managed' && skill.metadata?.source === 'clawhub') {
    badges.push('ClawHub');
  }
  if (skill.scope === 'workspace') {
    if (validationStatus) badges.push(validationStatus);
    const sourceCount = readSourceCount(skill);
    if (sourceCount > 0) badges.push(`${sourceCount} source${sourceCount === 1 ? '' : 's'}`);
  }
  return badges;
}

function validationTone(status: string): 'ok' | 'warn' | 'err' | 'muted' {
  if (status === 'verified') return 'ok';
  if (status === 'provisional') return 'warn';
  if (status === 'invalid') return 'err';
  return 'muted';
}

function workspaceReportSummary(report: WorkspaceSkillReport): string {
  if (report.kind === 'prompt_bundle') return 'Legacy workspace prompt bundle';
  if (report.validation.status === 'invalid') return 'Rejected during registration';
  if (report.validation.status === 'provisional') return 'Registered with validation warnings';
  return 'Registered and structurally verified';
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
    queryKey: ['skills'],
    queryFn: () => api.skills(),
    refetchInterval: 15_000,
  });

  const clawhubQuery = useQuery({
    queryKey: ['skills', 'clawhub'],
    queryFn: () => api.clawhubStatus(),
    refetchInterval: 60_000,
  });
  const workspaceValidationQuery = useQuery({
    queryKey: ['skills', 'workspaceValidation'],
    queryFn: () => api.workspaceSkillValidation(),
    refetchInterval: 30_000,
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

  const allSkills = data?.skills ?? [];
  const installedNames = useMemo(() => new Set(allSkills.map((skill) => skill.name)), [allSkills]);
  const selected = useMemo(
    () => allSkills.find((skill) => skill.name === selectedName) ?? null,
    [allSkills, selectedName]
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
    const counts: Record<ScopeName, number> = { bundled: 0, managed: 0, workspace: 0 };
    for (const skill of allSkills) counts[skill.scope as ScopeName] += 1;
    return counts;
  }, [allSkills]);

  const filtered = useMemo(() => {
    const needle = deferredSearch.trim().toLowerCase();
    return allSkills
      .filter((skill) => {
        if (scope && skill.scope !== scope) return false;
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
  }, [allSkills, deferredSearch, scope, sort]);

  const groupedSkills = useMemo(() => {
    const activeScopes = scope ? [scope as ScopeName] : [...SCOPE_ORDER];
    return activeScopes
      .map((groupScope) => ({
        scope: groupScope,
        items: filtered.filter((skill) => skill.scope === groupScope),
      }))
      .filter((group) => group.items.length > 0);
  }, [filtered, scope]);

  const installableCatalog = useMemo(
    () => catalog.filter((entry) => !installedNames.has(entry.name)),
    [catalog, installedNames]
  );
  const catalogPreview = useMemo(() => installableCatalog.slice(0, 8), [installableCatalog]);
  const catalogOverflow = Math.max(0, installableCatalog.length - catalogPreview.length);
  const installingName =
    installMutation.isPending && typeof installMutation.variables === 'string'
      ? installMutation.variables
      : null;
  const workspaceValidationData = workspaceValidationQuery.data;

  if (isLoading || !data) return <Skeleton height={320} />;

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 24 }}>
      <PageHeader
        eyebrow="Capabilities"
        title="Skills Registry"
        description="Installed capabilities, workspace prompt bundles, and the remote ClawHub catalog in one operational view."
        actions={
          <div className="inline">
            <button
              type="button"
              className="btn sm"
              disabled={syncMutation.isPending}
              onClick={() => syncMutation.mutate()}
            >
              <Icon path={paths.refresh} size={12} />
              {syncMutation.isPending ? 'Syncing…' : 'Sync catalog'}
            </button>
            <button
              type="button"
              className="btn ghost sm"
              disabled={rescanMutation.isPending}
              onClick={() => rescanMutation.mutate()}
            >
              <Icon path={paths.terminal} size={12} />
              {rescanMutation.isPending ? 'Rescanning…' : 'Rescan workspace'}
            </button>
          </div>
        }
      />

      <section className="grid grid-cols-4">
        <StatCard
          label="Installed"
          value={formatNumber(allSkills.length)}
          sub="active registry entries"
          iconPath={paths.box}
          accent="teal"
        />
        <StatCard
          label="Bundled"
          value={formatNumber(totals.bundled)}
          sub="native plugin skills"
          iconPath={paths.bot}
          accent="violet"
        />
        <StatCard
          label="Managed"
          value={formatNumber(totals.managed)}
          sub="registry-installed"
          iconPath={paths.sparkles}
          accent="cyan"
        />
        <StatCard
          label="Workspace"
          value={formatNumber(totals.workspace)}
          sub="prompt bundle injections"
          iconPath={paths.terminal}
          accent="amber"
        />
      </section>

      <Panel
        title="Local custom skill validation"
        tag={
          workspaceValidationData
            ? `${formatNumber(workspaceValidationData.counts.verified)} verified`
            : 'workspace'
        }
      >
        {workspaceValidationQuery.isLoading ? (
          <Skeleton height={180} />
        ) : workspaceValidationQuery.error ? (
          <div className="info-block">
            Validation unavailable: {toErrorMessage(workspaceValidationQuery.error)}
          </div>
        ) : !workspaceValidationData || workspaceValidationData.reports.length === 0 ? (
          <EmptyState
            title="No local workspace bundles found"
            description="Create `skills/<name>/SKILL.md` for custom local skills, or keep using AGENTS/SOUL/TOOLS prompt bundles."
          />
        ) : (
          <div className="stack-section">
            <div className="skills-chip-row">
              <span className="badge ok">
                {formatNumber(workspaceValidationData.counts.verified)} verified
              </span>
              <span className="badge warn">
                {formatNumber(workspaceValidationData.counts.provisional)} provisional
              </span>
              <span className="badge err">
                {formatNumber(workspaceValidationData.counts.invalid)} invalid
              </span>
            </div>

            <div className="cards-grid skills-grid">
              {workspaceValidationData.reports.map((report) => (
                <div key={`${report.kind}:${report.entrypoint}`} className="record-card skill-card">
                  <div className="skills-card-head">
                    <div className="skills-card-heading">
                      <div className="record-card-title mono">{report.name}</div>
                      <div className="skills-card-summary">{workspaceReportSummary(report)}</div>
                    </div>
                    <span className={`badge ${validationTone(report.validation.status)}`}>
                      {report.validation.status}
                    </span>
                  </div>

                  <div className="skills-card-entry mono" title={report.entrypoint}>
                    {report.entrypoint}
                  </div>

                  <div className="skills-card-meta">
                    <span className="badge muted">{report.kind}</span>
                    {report.validation.surfaces.map((surface) => (
                      <span key={surface} className="badge muted">
                        {surface}
                      </span>
                    ))}
                  </div>

                  {report.validation.errors.length > 0 && (
                    <div className="info-block" style={{ color: 'var(--accent-rose)' }}>
                      {report.validation.errors.join(' ')}
                    </div>
                  )}

                  {report.validation.warnings.length > 0 && (
                    <div className="info-block">{report.validation.warnings.join(' ')}</div>
                  )}
                </div>
              ))}
            </div>
          </div>
        )}
      </Panel>

      <section className="grid grid-split-2-1">
        <Panel title="Registry status" tag="ClawHub">
          <div className="detail-grid">
            <MetaTile
              label="Catalog"
              value={
                catalogQuery.isLoading
                  ? 'Loading…'
                  : `${formatNumber(installableCatalog.length)} installable`
              }
            />
            <MetaTile label="Base URL" value={clawhubQuery.data?.base_url ?? '—'} />
            <MetaTile label="Install dir" value={clawhubQuery.data?.install_dir ?? '—'} />
            <MetaTile
              label="Auth"
              value={clawhubQuery.data?.auth_token_set ? 'Configured' : 'Anonymous'}
            />
          </div>

          <div className="skills-callout">
            <div className="skills-callout-title">How this registry is composed</div>
            <div className="skills-callout-body">
              Bundled skills are shipped with Baselithbot, managed skills are installed from
              ClawHub, and workspace skills are generated from `AGENTS.md`, `SOUL.md`, and
              `TOOLS.md` bundles found in the active state directories.
            </div>
          </div>

          <div className="skills-chip-row">
            <span className="badge muted">{formatNumber(allSkills.length)} installed</span>
            <span className="badge ok">{formatNumber(totals.managed)} managed</span>
            <span className="badge warn">{formatNumber(totals.workspace)} workspace</span>
            <span className="badge muted">{formatNumber(totals.bundled)} bundled</span>
          </div>
        </Panel>

        <Panel title="Quick install" tag="manual">
          <div className="skills-install-panel">
            <div className="form-row">
              <label htmlFor="skill-install-name">Install by exact skill name</label>
              <input
                id="skill-install-name"
                className="input"
                placeholder="e.g. my-skill"
                value={installName}
                onChange={(event) => setInstallName(event.target.value)}
              />
            </div>

            <button
              type="button"
              className="btn primary"
              disabled={!installName.trim() || installMutation.isPending}
              onClick={() => installMutation.mutate(installName.trim())}
            >
              <Icon path={paths.plus} size={12} />
              {installMutation.isPending ? 'Installing…' : 'Install skill'}
            </button>

            <div className="info-block">
              Use this when you already know the package name. For browsing, use the curated catalog
              section below.
            </div>
          </div>
        </Panel>
      </section>

      <Panel
        title="Installable from ClawHub"
        tag={`${formatNumber(installableCatalog.length)} available`}
      >
        {catalogQuery.error ? (
          <div className="info-block">
            Catalog unavailable: {toErrorMessage(catalogQuery.error)}
          </div>
        ) : catalogQuery.isLoading ? (
          <Skeleton height={200} />
        ) : installableCatalog.length === 0 ? (
          <EmptyState
            title="No installable catalog entries"
            description="Either the registry is empty or every catalog skill is already installed locally."
          />
        ) : (
          <div className="stack-section">
            {catalogOverflow > 0 && (
              <div className="info-block">
                Showing the first {catalogPreview.length} installable entries. Use quick install if
                you need a specific package by name.
              </div>
            )}

            <div className="cards-grid skills-grid">
              {catalogPreview.map((entry) => (
                <div key={entry.name} className="record-card skill-card catalog-skill-card">
                  <div className="skills-card-head">
                    <div className="skills-card-heading">
                      <div className="record-card-title mono">{entry.name}</div>
                      <div className="skills-card-summary">
                        Available from the remote ClawHub registry
                      </div>
                    </div>
                    <span className="badge ok">catalog</span>
                  </div>

                  <div className="skills-card-description">
                    {entry.description || 'No description provided for this catalog entry.'}
                  </div>

                  <div className="skills-card-meta">
                    <span className="badge muted mono">v{entry.version}</span>
                    {entry.entrypoint && <span className="badge muted">entrypoint declared</span>}
                  </div>

                  <div className="skills-card-entry mono" title={entry.entrypoint ?? undefined}>
                    {entry.entrypoint || 'Remote package artifact'}
                  </div>

                  <div className="skills-card-actions">
                    <button
                      type="button"
                      className="btn primary sm"
                      disabled={installMutation.isPending}
                      onClick={() => installMutation.mutate(entry.name)}
                    >
                      <Icon path={paths.plus} size={12} />
                      {installingName === entry.name ? 'Installing…' : 'Install'}
                    </button>
                  </div>
                </div>
              ))}
            </div>
          </div>
        )}
      </Panel>

      <Panel title="Installed skills" tag={`${formatNumber(filtered.length)} visible`}>
        <div className="channel-toolbar">
          <input
            className="input channel-toolbar-search"
            placeholder="Search by name, scope, version, or description…"
            value={search}
            onChange={(event) => setSearch(event.target.value)}
          />

          <div className="channel-toolbar-controls">
            <select
              className="select"
              value={scope}
              onChange={(event) => setScope(event.target.value as (typeof SCOPES)[number])}
            >
              <option value="">All scopes</option>
              <option value="bundled">Bundled</option>
              <option value="managed">Managed</option>
              <option value="workspace">Workspace</option>
            </select>

            <select
              className="select"
              value={sort}
              onChange={(event) => setSort(event.target.value as SortKey)}
            >
              <option value="name">Sort by name</option>
              <option value="scope">Sort by scope</option>
              <option value="version">Sort by version</option>
            </select>
          </div>
        </div>

        <div className="skills-toolbar-summary">
          <span className="badge muted">{formatNumber(filtered.length)} visible</span>
          <span className="badge muted">{formatNumber(allSkills.length)} total</span>
          <span className="badge ok">{formatNumber(totals.managed)} managed</span>
          <span className="badge warn">{formatNumber(totals.workspace)} workspace</span>
          <span className="badge muted">{formatNumber(totals.bundled)} bundled</span>
        </div>

        {filtered.length === 0 ? (
          <EmptyState
            title={allSkills.length === 0 ? 'No skills installed' : 'No skills match'}
            description={
              allSkills.length === 0
                ? 'Install from ClawHub or rescan workspace bundles to populate the registry.'
                : 'Adjust the search query or scope filter to broaden the result set.'
            }
          />
        ) : (
          <div className="skills-sections">
            {groupedSkills.map((group) => (
              <section key={group.scope} className="skills-section">
                <div className="skills-section-head">
                  <div>
                    <div className="skills-section-title">{scopeLabel(group.scope)}</div>
                    <div className="skills-section-sub">
                      {formatNumber(group.items.length)} visible in this scope
                    </div>
                  </div>
                  <span className={`badge ${scopeTone(group.scope)}`}>{group.scope}</span>
                </div>

                <div className="cards-grid skills-grid">
                  {group.items.map((skill) => (
                    <button
                      key={skill.name}
                      type="button"
                      className="record-card skill-card"
                      onClick={() => setSelectedName(skill.name)}
                    >
                      <div className="skills-card-head">
                        <div className="skills-card-heading">
                          <div className="record-card-title mono">{skill.name}</div>
                          <div className="skills-card-summary">{skillSummary(skill)}</div>
                        </div>
                        <span className={`badge ${scopeTone(skill.scope as ScopeName)}`}>
                          {skill.scope}
                        </span>
                      </div>

                      <div className="skills-card-description">
                        {skill.description || 'No description provided for this skill entry.'}
                      </div>

                      <div className="skills-card-meta">
                        {skillMetaBadges(skill).map((badge) => (
                          <span key={badge} className="badge muted mono">
                            {badge}
                          </span>
                        ))}
                      </div>

                      <div className="skills-card-entry mono" title={skill.entrypoint ?? undefined}>
                        {skill.entrypoint || 'No entrypoint declared'}
                      </div>
                    </button>
                  ))}
                </div>
              </section>
            ))}
          </div>
        )}
      </Panel>

      <DetailDrawer
        open={!!selected}
        title={selected?.name ?? ''}
        subtitle={
          selected ? `${scopeLabel(selected.scope as ScopeName)} skill details` : 'Skill details'
        }
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
                  <Icon path={paths.trash} size={12} />
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

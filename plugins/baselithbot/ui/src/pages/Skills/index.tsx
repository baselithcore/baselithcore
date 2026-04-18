import { useDeferredValue, useMemo, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { api, type Skill } from '../../lib/api';
import { useConfirm } from '../../components/ConfirmProvider';
import { PageHeader } from '../../components/PageHeader';
import { Skeleton } from '../../components/Skeleton';
import { StatCard } from '../../components/StatCard';
import { useToasts } from '../../components/ToastProvider';
import { formatNumber } from '../../lib/format';
import { Icon, paths } from '../../lib/icons';
import {
  SCOPES,
  SCOPE_ORDER,
  normalizeCatalogEntry,
  toErrorMessage,
  type CatalogSkill,
  type ScopeName,
  type SortKey,
} from './helpers';
import { ClawhubCatalog } from './sections/ClawhubCatalog';
import { InstalledSkills } from './sections/InstalledSkills';
import { RegistryQuickInstall } from './sections/RegistryQuickInstall';
import { SkillDetail } from './sections/SkillDetail';
import { ValidationPanel } from './sections/ValidationPanel';

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

      <InstalledSkills
        allSkills={allSkills}
        filtered={filtered}
        groupedSkills={groupedSkills}
        totals={totals}
        search={search}
        setSearch={setSearch}
        scope={scope}
        setScope={setScope}
        sort={sort}
        setSort={setSort}
        setSelectedName={setSelectedName}
      />

      <ValidationPanel
        isLoading={workspaceValidationQuery.isLoading}
        error={workspaceValidationQuery.error}
        data={workspaceValidationQuery.data}
      />

      <RegistryQuickInstall
        allSkills={allSkills}
        totals={totals}
        installableCount={installableCatalog.length}
        catalogLoading={catalogQuery.isLoading}
        clawhubBaseUrl={clawhubQuery.data?.base_url}
        clawhubInstallDir={clawhubQuery.data?.install_dir}
        clawhubAuthSet={clawhubQuery.data?.auth_token_set}
        installName={installName}
        setInstallName={setInstallName}
        installPending={installMutation.isPending}
        onInstall={(name) => installMutation.mutate(name)}
      />

      <ClawhubCatalog
        error={catalogQuery.error}
        isLoading={catalogQuery.isLoading}
        installableCatalog={installableCatalog}
        catalogPreview={catalogPreview}
        catalogOverflow={catalogOverflow}
        installingName={installingName}
        installPending={installMutation.isPending}
        onInstall={(name) => installMutation.mutate(name)}
      />

      <SkillDetail
        selected={selected}
        onClose={() => setSelectedName(null)}
        onRequestRemove={requestRemove}
        removePending={removeMutation.isPending}
      />
    </div>
  );
}

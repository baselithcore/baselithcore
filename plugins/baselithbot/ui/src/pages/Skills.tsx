import { useDeferredValue, useMemo, useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { api, type Skill } from '../lib/api';
import { DetailDrawer } from '../components/DetailDrawer';
import { EmptyState } from '../components/EmptyState';
import { PageHeader } from '../components/PageHeader';
import { Panel } from '../components/Panel';
import { Skeleton } from '../components/Skeleton';
import { formatNumber } from '../lib/format';

const SCOPES = ['', 'bundled', 'managed', 'workspace'] as const;

type SortKey = 'name' | 'scope' | 'version';

export function Skills() {
  const [scope, setScope] = useState<(typeof SCOPES)[number]>('');
  const [search, setSearch] = useState('');
  const [sort, setSort] = useState<SortKey>('name');
  const [selectedName, setSelectedName] = useState<string | null>(null);
  const deferredSearch = useDeferredValue(search);

  const { data, isLoading } = useQuery({
    queryKey: ['skills', scope],
    queryFn: () => api.skills(scope || undefined),
    refetchInterval: 15_000,
  });

  const skills = data?.skills ?? [];
  const selected = useMemo(
    () => skills.find((skill) => skill.name === selectedName) ?? null,
    [selectedName, skills]
  );

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

  if (skills.length === 0)
    return (
      <>
        <PageHeader
          eyebrow="Capabilities"
          title="Skills registry"
          description="Reusable skills available to the bot."
        />
        <EmptyState
          title="No skills installed"
          description="Register skills via the SkillRegistry to surface them here."
        />
      </>
    );

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 24 }}>
      <PageHeader
        eyebrow="Capabilities"
        title="Skills registry"
        description={`${formatNumber(skills.length)} skills available across bundled, managed, and workspace scopes.`}
      />

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
            title="No skills match"
            description="Try another search term or switch to a broader scope."
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

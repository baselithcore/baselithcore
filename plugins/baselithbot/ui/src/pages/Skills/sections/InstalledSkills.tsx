import { EmptyState } from '../../../components/EmptyState';
import { Panel } from '../../../components/Panel';
import type { Skill } from '../../../lib/api';
import { formatNumber } from '../../../lib/format';
import {
  SCOPES,
  scopeLabel,
  scopeTone,
  skillMetaBadges,
  skillSummary,
  type ScopeName,
  type SortKey,
} from '../helpers';

interface InstalledSkillsProps {
  allSkills: Skill[];
  filtered: Skill[];
  groupedSkills: { scope: ScopeName; items: Skill[] }[];
  totals: Record<ScopeName, number>;
  search: string;
  setSearch: (value: string) => void;
  scope: (typeof SCOPES)[number];
  setScope: (value: (typeof SCOPES)[number]) => void;
  sort: SortKey;
  setSort: (value: SortKey) => void;
  setSelectedName: (name: string | null) => void;
}

export function InstalledSkills({
  allSkills,
  filtered,
  groupedSkills,
  totals,
  search,
  setSearch,
  scope,
  setScope,
  sort,
  setSort,
  setSelectedName,
}: InstalledSkillsProps) {
  return (
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
  );
}

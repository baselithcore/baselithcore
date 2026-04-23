import { Panel } from '../../../components/Panel';
import type { Skill } from '../../../lib/api';
import { formatNumber } from '../../../lib/format';
import { Icon, paths } from '../../../lib/icons';
import { MetaTile } from '../components';
import type { ScopeName } from '../helpers';

interface RegistryQuickInstallProps {
  allSkills: Skill[];
  totals: Record<ScopeName, number>;
  installableCount: number;
  catalogLoading: boolean;
  clawhubBaseUrl: string | undefined;
  clawhubInstallDir: string | undefined;
  clawhubAuthSet: boolean | undefined;
  installName: string;
  setInstallName: (value: string) => void;
  installPending: boolean;
  onInstall: (name: string) => void;
}

export function RegistryQuickInstall({
  allSkills,
  totals,
  installableCount,
  catalogLoading,
  clawhubBaseUrl,
  clawhubInstallDir,
  clawhubAuthSet,
  installName,
  setInstallName,
  installPending,
  onInstall,
}: RegistryQuickInstallProps) {
  return (
    <section className="grid grid-split-2-1">
      <Panel title="Registry status" tag="ClawHub">
        <div className="detail-grid">
          <MetaTile
            label="Catalog"
            value={catalogLoading ? 'Loading…' : `${formatNumber(installableCount)} installable`}
          />
          <MetaTile label="Base URL" value={clawhubBaseUrl ?? '—'} />
          <MetaTile label="Install dir" value={clawhubInstallDir ?? '—'} />
          <MetaTile label="Auth" value={clawhubAuthSet ? 'Configured' : 'Anonymous'} />
        </div>

        <div className="skills-callout">
          <div className="skills-callout-title">How this registry is composed</div>
          <div className="skills-callout-body">
            Bundled skills are shipped with Baselithbot, managed skills are installed from ClawHub,
            and workspace skills are generated from `AGENTS.md`, `SOUL.md`, and `TOOLS.md` bundles
            found in the active state directories.
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
            disabled={!installName.trim() || installPending}
            onClick={() => onInstall(installName.trim())}
          >
            <Icon path={paths.plus} size={12} />
            {installPending ? 'Installing…' : 'Install skill'}
          </button>

          <div className="info-block">
            Use this when you already know the package name. For browsing, use the curated catalog
            section below.
          </div>
        </div>
      </Panel>
    </section>
  );
}

import { StatCard } from '../../../components/StatCard';
import type { StealthConfig } from '../../../lib/api';
import { formatNumber } from '../../../lib/format';
import { paths } from '../../../lib/icons';
import { TOGGLE_FIELDS } from '../helpers';

type StatsRowProps = {
  draftConfig: StealthConfig;
  enabledTogglesCount: number;
  normalizedLanguages: string[];
  normalizedUserAgents: string[];
};

export function StatsRow({
  draftConfig,
  enabledTogglesCount,
  normalizedLanguages,
  normalizedUserAgents,
}: StatsRowProps) {
  return (
    <section className="grid grid-cols-4">
      <StatCard
        label="Toggles On"
        value={`${enabledTogglesCount}/${TOGGLE_FIELDS.length}`}
        sub="stealth controls active"
        iconPath={paths.shield}
        accent="teal"
      />
      <StatCard
        label="UA Pool"
        value={formatNumber(normalizedUserAgents.length)}
        sub={draftConfig.rotate_user_agent ? 'rotation candidates' : 'configured identities'}
        iconPath={paths.refresh}
        accent="amber"
      />
      <StatCard
        label="Locales"
        value={formatNumber(normalizedLanguages.length)}
        sub="header + navigator override set"
        iconPath={paths.sparkles}
        accent="violet"
      />
      <StatCard
        label="Timezone"
        value={draftConfig.spoof_timezone.trim() || 'Unset'}
        sub={draftConfig.enabled ? 'Playwright context setting' : 'inactive while disabled'}
        iconPath={paths.clock}
        accent={draftConfig.spoof_timezone.trim() ? 'cyan' : 'rose'}
      />
    </section>
  );
}

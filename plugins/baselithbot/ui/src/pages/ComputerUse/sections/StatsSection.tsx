import { StatCard } from '../../../components/StatCard';
import { type ComputerUseConfig } from '../../../lib/api';
import { formatNumber } from '../../../lib/format';
import { paths } from '../../../lib/icons';
import { CAPABILITY_FIELDS, type CapabilitySpec } from '../helpers';

type StatsSectionProps = {
  draftConfig: ComputerUseConfig;
  enabledCapabilities: CapabilitySpec[];
  enabledToolCount: number;
  normalizedAllowlist: string[];
  auditConfigured: boolean;
};

export function StatsSection({
  draftConfig,
  enabledCapabilities,
  enabledToolCount,
  normalizedAllowlist,
  auditConfigured,
}: StatsSectionProps) {
  return (
    <section className="grid grid-cols-4">
      <StatCard
        label="Capabilities On"
        value={`${enabledCapabilities.length}/${CAPABILITY_FIELDS.length}`}
        sub="feature gates raised"
        iconPath={paths.shield}
        accent="teal"
      />
      <StatCard
        label="Tool Surface"
        value={formatNumber(enabledToolCount)}
        sub="MCP tools reachable"
        iconPath={paths.sparkles}
        accent="cyan"
      />
      <StatCard
        label="Allowlist Entries"
        value={formatNumber(normalizedAllowlist.length)}
        sub={draftConfig.allow_shell ? 'shell routing policy' : 'shell capability off'}
        iconPath={paths.terminal}
        accent="amber"
      />
      <StatCard
        label="Audit State"
        value={auditConfigured ? 'Persisted' : 'Ephemeral'}
        sub={auditConfigured ? 'JSONL path configured' : 'no on-disk trail'}
        iconPath={paths.activity}
        accent={auditConfigured ? 'violet' : 'rose'}
      />
    </section>
  );
}

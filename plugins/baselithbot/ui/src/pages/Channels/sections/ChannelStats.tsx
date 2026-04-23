import { StatCard } from '../../../components/StatCard';
import { formatNumber } from '../../../lib/format';
import { paths } from '../../../lib/icons';

interface ChannelStatsProps {
  total: number;
  configuredCount: number;
  missingCount: number;
  totalEvents: number;
  liveCount: number;
}

export function ChannelStats({
  total,
  configuredCount,
  missingCount,
  totalEvents,
  liveCount,
}: ChannelStatsProps) {
  return (
    <section className="grid grid-cols-4">
      <StatCard
        label="Registered"
        value={formatNumber(total)}
        sub="available channel adapters"
        iconPath={paths.cable}
        accent="cyan"
      />
      <StatCard
        label="Configured"
        value={formatNumber(configuredCount)}
        sub="ready for start or test"
        iconPath={paths.check}
        accent="teal"
      />
      <StatCard
        label="Needs config"
        value={formatNumber(missingCount)}
        sub="missing required credentials"
        iconPath={paths.shield}
        accent="amber"
      />
      <StatCard
        label="Inbound events"
        value={formatNumber(totalEvents)}
        sub={`${formatNumber(liveCount)} live adapters`}
        iconPath={paths.activity}
        accent="violet"
      />
    </section>
  );
}

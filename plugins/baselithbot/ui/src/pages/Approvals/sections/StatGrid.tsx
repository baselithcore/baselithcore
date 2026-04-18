import { StatCard } from '../../../components/StatCard';
import { formatNumber } from '../../../lib/format';
import { paths } from '../../../lib/icons';

interface StatGridProps {
  pendingCount: number;
  approvedCount: number;
  deniedCount: number;
  timedOutCount: number;
}

export function StatGrid({
  pendingCount,
  approvedCount,
  deniedCount,
  timedOutCount,
}: StatGridProps) {
  return (
    <section className="grid grid-cols-4">
      <StatCard
        label="Pending Queue"
        value={formatNumber(pendingCount)}
        sub="live approvals waiting for operator input"
        iconPath={paths.heart}
        accent="amber"
      />
      <StatCard
        label="Approved"
        value={formatNumber(approvedCount)}
        sub="resolved successfully in the visible window"
        iconPath={paths.check}
        accent="teal"
      />
      <StatCard
        label="Denied"
        value={formatNumber(deniedCount)}
        sub="rejected by operator"
        iconPath={paths.shieldOff}
        accent="rose"
      />
      <StatCard
        label="Timed Out"
        value={formatNumber(timedOutCount)}
        sub="auto-denied after timeout"
        iconPath={paths.clock}
        accent="violet"
      />
    </section>
  );
}

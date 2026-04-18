import { StatCard } from '../../../components/StatCard';
import { paths } from '../../../lib/icons';
import { formatCost, formatMs, formatNumber, truncate } from '../../../lib/format';

interface Props {
  eventCount: number;
  modelRowsLength: number;
  totalTokens: number;
  tokenSplit: { prompt: number; completion: number };
  totalCost: number;
  costPer1k: number;
  avgLatencyMs: number;
  topModel: string | undefined;
}

export function StatCards({
  eventCount,
  modelRowsLength,
  totalTokens,
  tokenSplit,
  totalCost,
  costPer1k,
  avgLatencyMs,
  topModel,
}: Props) {
  return (
    <section className="grid grid-cols-4">
      <StatCard
        label="Events in buffer"
        value={formatNumber(eventCount)}
        sub={`${modelRowsLength} model${modelRowsLength === 1 ? '' : 's'} observed`}
        iconPath={paths.activity}
        accent="teal"
      />
      <StatCard
        label="Total tokens"
        value={formatNumber(totalTokens)}
        sub={
          tokenSplit.prompt + tokenSplit.completion > 0
            ? `${formatNumber(tokenSplit.prompt)} in · ${formatNumber(tokenSplit.completion)} out`
            : 'prompt · completion'
        }
        iconPath={paths.sparkles}
        accent="cyan"
      />
      <StatCard
        label="Total cost"
        value={formatCost(totalCost)}
        sub={costPer1k > 0 ? `${formatCost(costPer1k)} / 1k tokens` : 'buffer aggregate'}
        iconPath={paths.coin}
        accent="amber"
      />
      <StatCard
        label="Avg latency"
        value={formatMs(avgLatencyMs)}
        sub={topModel ? `top: ${truncate(topModel, 22)}` : 'mean across events'}
        iconPath={paths.bolt}
        accent="violet"
      />
    </section>
  );
}

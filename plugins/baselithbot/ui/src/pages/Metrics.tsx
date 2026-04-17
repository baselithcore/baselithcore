import { useMemo } from 'react';
import { useQuery } from '@tanstack/react-query';
import { api } from '../lib/api';
import { PageHeader } from '../components/PageHeader';
import { Panel } from '../components/Panel';
import { StatCard } from '../components/StatCard';
import { EmptyState } from '../components/EmptyState';
import { Skeleton } from '../components/Skeleton';
import { paths } from '../lib/icons';
import { formatCost, formatMs, formatNumber, truncate } from '../lib/format';

export function Metrics() {
  const usage = useQuery({
    queryKey: ['usageSummary'],
    queryFn: api.usageSummary,
    refetchInterval: 15_000,
  });

  const prometheus = useQuery({
    queryKey: ['prometheus'],
    queryFn: api.prometheus,
    refetchInterval: 30_000,
  });

  const rows = useMemo(
    () => Object.entries(usage.data?.by_model ?? {}).sort((a, b) => b[1].tokens - a[1].tokens),
    [usage.data]
  );

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 24 }}>
      <PageHeader
        eyebrow="Telemetry"
        title="Metrics"
        description="Usage ledger summary, per-model cost breakdown, and Prometheus exposition passthrough."
      />

      {(usage.isLoading || prometheus.isLoading) && <Skeleton height={220} />}

      {usage.data && (
        <section className="grid grid-cols-4">
          <StatCard
            label="Events in buffer"
            value={formatNumber(usage.data.events_in_buffer)}
            sub="usage ledger recent window"
            iconPath={paths.activity}
            accent="teal"
          />
          <StatCard
            label="Total tokens"
            value={formatNumber(usage.data.total_tokens)}
            sub={`${rows.length} models observed`}
            iconPath={paths.sparkles}
            accent="cyan"
          />
          <StatCard
            label="Total cost"
            value={formatCost(usage.data.total_cost_usd)}
            sub="buffer aggregate"
            iconPath={paths.coin}
            accent="amber"
          />
          <StatCard
            label="Avg latency"
            value={formatMs(usage.data.avg_latency_ms)}
            sub="mean across recent events"
            iconPath={paths.bolt}
            accent="violet"
          />
        </section>
      )}

      {usage.data && (
        <section className="grid grid-split-1-2">
          <Panel title="By model" tag={`${rows.length}`}>
            {rows.length === 0 ? (
              <EmptyState
                title="No model usage yet"
                description="Per-model rows will appear after the usage ledger records events."
              />
            ) : (
              <table className="table">
                <thead>
                  <tr>
                    <th>Model</th>
                    <th>Events</th>
                    <th>Tokens</th>
                    <th>Cost</th>
                  </tr>
                </thead>
                <tbody>
                  {rows.map(([model, row]) => (
                    <tr key={model}>
                      <td className="mono" style={{ color: 'var(--ink-100)' }}>
                        {model}
                      </td>
                      <td className="mono">{formatNumber(row.events)}</td>
                      <td className="mono">{formatNumber(row.tokens)}</td>
                      <td className="mono">{formatCost(row.cost_usd)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
          </Panel>

          <Panel title="Prometheus" tag={prometheus.data?.available ? 'available' : 'fallback'}>
            {!prometheus.data ? (
              <EmptyState
                title="Metrics unavailable"
                description="Prometheus exposition could not be loaded from the dashboard passthrough."
              />
            ) : (
              <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
                <div className="info-block">
                  {prometheus.data.available
                    ? 'Prometheus client is installed and exporting metrics.'
                    : 'Prometheus client is not installed. The passthrough currently returns the fallback text payload.'}
                </div>
                <pre className="code-block">
                  {truncate(prometheus.data.text, 4000) || '# empty payload'}
                </pre>
              </div>
            )}
          </Panel>
        </section>
      )}
    </div>
  );
}

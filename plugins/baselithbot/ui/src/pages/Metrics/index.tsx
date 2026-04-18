import { useMemo, useState } from 'react';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { api } from '../../lib/api';
import { PageHeader } from '../../components/PageHeader';
import { Skeleton } from '../../components/Skeleton';
import { Icon, paths } from '../../lib/icons';
import { useToasts } from '../../components/ToastProvider';
import { formatRelative } from '../../lib/format';
import { type SortKey } from './helpers';
import { StatCards } from './sections/StatCards';
import { UsageTrendPanel } from './sections/UsageTrendPanel';
import { ByModelPanel } from './sections/ByModelPanel';
import { PrometheusPanel } from './sections/PrometheusPanel';

export function Metrics() {
  const qc = useQueryClient();
  const { push } = useToasts();

  const usage = useQuery({
    queryKey: ['usageSummary'],
    queryFn: api.usageSummary,
    refetchInterval: 15_000,
  });

  const usageRecent = useQuery({
    queryKey: ['usageRecent', 120],
    queryFn: () => api.usageRecent(120),
    refetchInterval: 15_000,
  });

  const prometheus = useQuery({
    queryKey: ['prometheus'],
    queryFn: api.prometheus,
    refetchInterval: 30_000,
  });

  const [sortKey, setSortKey] = useState<SortKey>('tokens');
  const [filter, setFilter] = useState('');
  const [promExpanded, setPromExpanded] = useState(false);

  const modelRows = useMemo(() => {
    const entries = Object.entries(usage.data?.by_model ?? {});
    const filtered = filter.trim()
      ? entries.filter(([m]) => m.toLowerCase().includes(filter.trim().toLowerCase()))
      : entries;
    return filtered.sort((a, b) => {
      if (sortKey === 'cost') return b[1].cost_usd - a[1].cost_usd;
      if (sortKey === 'events') return b[1].events - a[1].events;
      return b[1].tokens - a[1].tokens;
    });
  }, [usage.data, sortKey, filter]);

  const totalTokens = usage.data?.total_tokens ?? 0;
  const totalCost = usage.data?.total_cost_usd ?? 0;
  const eventCount = usage.data?.events_in_buffer ?? 0;
  const topModel = modelRows[0]?.[0];

  const tokenSplit = useMemo(() => {
    const evs = usageRecent.data?.events ?? [];
    let prompt = 0;
    let completion = 0;
    for (const e of evs) {
      prompt += e.prompt_tokens;
      completion += e.completion_tokens;
    }
    return { prompt, completion };
  }, [usageRecent.data]);

  const costPer1k = totalTokens > 0 ? (totalCost / totalTokens) * 1000 : 0;

  const chartData = useMemo(() => {
    const evs = usageRecent.data?.events ?? [];
    return {
      labels: evs.map((_, i) => String(i + 1)),
      datasets: [
        {
          label: 'tokens',
          data: evs.map((e) => e.total_tokens),
          borderColor: '#2ee6c4',
          backgroundColor: 'rgba(46, 230, 196, 0.18)',
          pointRadius: 0,
          tension: 0.3,
          fill: true,
          yAxisID: 'y',
        },
        {
          label: 'latency (ms)',
          data: evs.map((e) => e.latency_ms),
          borderColor: '#a78bfa',
          backgroundColor: 'rgba(167, 139, 250, 0.12)',
          pointRadius: 0,
          tension: 0.3,
          fill: true,
          yAxisID: 'y1',
        },
      ],
    };
  }, [usageRecent.data]);

  const refreshAll = () => {
    qc.invalidateQueries({ queryKey: ['usageSummary'] });
    qc.invalidateQueries({ queryKey: ['usageRecent', 120] });
    qc.invalidateQueries({ queryKey: ['prometheus'] });
  };

  const copyProm = async () => {
    const text = prometheus.data?.text ?? '';
    try {
      await navigator.clipboard.writeText(text);
      push({
        tone: 'success',
        title: 'Prometheus payload copied',
        description: `${text.length} chars`,
      });
    } catch (err) {
      push({
        tone: 'error',
        title: 'Copy failed',
        description: err instanceof Error ? err.message : String(err),
      });
    }
  };

  const isLoading = usage.isLoading && !usage.data;
  const isRefreshing = usage.isFetching || usageRecent.isFetching || prometheus.isFetching;
  const lastUpdated = usage.dataUpdatedAt ? Math.floor(usage.dataUpdatedAt / 1000) : 0;

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 24 }}>
      <PageHeader
        eyebrow="Telemetry"
        title="Metrics"
        description="Live usage ledger, per-model cost breakdown, and Prometheus exposition."
        actions={
          <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
            <span className={`pill ${isRefreshing ? 'ok' : ''}`} title="Auto-refresh 15s">
              <span className="dot" />
              {isRefreshing ? 'syncing' : lastUpdated ? formatRelative(lastUpdated) : 'idle'}
            </span>
            <button
              className="btn sm ghost"
              onClick={refreshAll}
              disabled={isRefreshing}
              title="Refresh now"
            >
              <Icon path={paths.refresh} size={14} />
              Refresh
            </button>
          </div>
        }
      />

      {isLoading && (
        <section className="grid grid-cols-4">
          {Array.from({ length: 4 }).map((_, i) => (
            <Skeleton key={i} height={112} />
          ))}
        </section>
      )}

      {usage.isError && (
        <div className="run-error">
          Failed to load usage summary: {(usage.error as Error)?.message ?? 'unknown error'}
        </div>
      )}

      {usage.data && (
        <>
          <StatCards
            eventCount={eventCount}
            modelRowsLength={modelRows.length}
            totalTokens={totalTokens}
            tokenSplit={tokenSplit}
            totalCost={totalCost}
            costPer1k={costPer1k}
            avgLatencyMs={usage.data.avg_latency_ms}
            topModel={topModel}
          />

          <UsageTrendPanel
            events={usageRecent.data?.events ?? []}
            chartData={chartData}
            modelRows={modelRows}
            distributionTotal={
              sortKey === 'cost' ? totalCost : sortKey === 'events' ? eventCount : totalTokens
            }
            sortKey={sortKey}
          />

          <ByModelPanel
            modelRows={modelRows}
            filter={filter}
            setFilter={setFilter}
            sortKey={sortKey}
            setSortKey={setSortKey}
            totalTokens={totalTokens}
            totalCost={totalCost}
            eventCount={eventCount}
          />

          <PrometheusPanel
            data={prometheus.data}
            promExpanded={promExpanded}
            setPromExpanded={setPromExpanded}
            copyProm={copyProm}
          />
        </>
      )}
    </div>
  );
}

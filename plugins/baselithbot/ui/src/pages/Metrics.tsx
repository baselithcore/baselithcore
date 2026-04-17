import { useMemo, useState } from 'react';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { Line } from 'react-chartjs-2';
import {
  CategoryScale,
  Chart as ChartJS,
  Filler,
  LinearScale,
  LineElement,
  PointElement,
  Tooltip,
} from 'chart.js';
import { api } from '../lib/api';
import { PageHeader } from '../components/PageHeader';
import { Panel } from '../components/Panel';
import { StatCard } from '../components/StatCard';
import { EmptyState } from '../components/EmptyState';
import { Skeleton } from '../components/Skeleton';
import { Icon, paths } from '../lib/icons';
import { useToasts } from '../components/ToastProvider';
import { formatCost, formatMs, formatNumber, formatRelative, truncate } from '../lib/format';

ChartJS.register(CategoryScale, LinearScale, PointElement, LineElement, Tooltip, Filler);

type SortKey = 'tokens' | 'cost' | 'events';

const SORT_LABELS: Record<SortKey, string> = {
  tokens: 'Tokens',
  cost: 'Cost',
  events: 'Events',
};

const ACCENTS = ['teal', 'violet', 'cyan', 'amber', 'rose'] as const;

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
          <section className="grid grid-cols-4">
            <StatCard
              label="Events in buffer"
              value={formatNumber(eventCount)}
              sub={`${modelRows.length} model${modelRows.length === 1 ? '' : 's'} observed`}
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
              value={formatMs(usage.data.avg_latency_ms)}
              sub={topModel ? `top: ${truncate(topModel, 22)}` : 'mean across events'}
              iconPath={paths.bolt}
              accent="violet"
            />
          </section>

          <section className="grid grid-split-2-1">
            <Panel title="Usage trend" tag="tokens · latency">
              {(usageRecent.data?.events ?? []).length === 0 ? (
                <EmptyState
                  title="No usage events yet"
                  description="The chart populates as the UsageLedger records events."
                />
              ) : (
                <div className="chart-wrap">
                  <Line
                    data={chartData}
                    options={{
                      responsive: true,
                      maintainAspectRatio: false,
                      interaction: { intersect: false, mode: 'index' },
                      plugins: {
                        tooltip: {
                          backgroundColor: '#0f1319',
                          borderColor: '#2e3644',
                          borderWidth: 1,
                          titleColor: '#dde1ea',
                          bodyColor: '#b4bccb',
                        },
                      },
                      scales: {
                        x: {
                          ticks: { color: '#7a8396', maxTicksLimit: 6 },
                          grid: { color: 'rgba(46,53,69,0.4)' },
                        },
                        y: {
                          position: 'left',
                          ticks: { color: '#7a8396' },
                          grid: { color: 'rgba(46,53,69,0.25)' },
                        },
                        y1: {
                          position: 'right',
                          ticks: { color: '#7a8396' },
                          grid: { drawOnChartArea: false },
                        },
                      },
                    }}
                  />
                </div>
              )}
            </Panel>

            <Panel title="Distribution" tag={`${modelRows.length}`}>
              <ModelDistribution
                rows={modelRows.slice(0, 6)}
                total={
                  sortKey === 'cost' ? totalCost : sortKey === 'events' ? eventCount : totalTokens
                }
                sortKey={sortKey}
              />
            </Panel>
          </section>

          <Panel title="By model" tag={`${modelRows.length}`} className="metrics-models-panel">
            <div
              style={{
                display: 'flex',
                flexWrap: 'wrap',
                alignItems: 'center',
                gap: 12,
                marginBottom: 16,
              }}
            >
              <input
                className="input"
                type="search"
                placeholder="Filter models…"
                value={filter}
                onChange={(e) => setFilter(e.target.value)}
                style={{ flex: '1 1 220px', maxWidth: 320 }}
              />
              <div style={{ display: 'flex', gap: 6 }}>
                {(Object.keys(SORT_LABELS) as SortKey[]).map((k) => (
                  <button
                    key={k}
                    className={`btn xs ${sortKey === k ? 'primary' : 'ghost'}`}
                    onClick={() => setSortKey(k)}
                  >
                    {SORT_LABELS[k]}
                  </button>
                ))}
              </div>
            </div>

            {modelRows.length === 0 ? (
              <EmptyState
                title={filter ? 'No match' : 'No model usage yet'}
                description={
                  filter
                    ? 'Clear or adjust the filter to see models.'
                    : 'Per-model rows will appear after the usage ledger records events.'
                }
              />
            ) : (
              <div style={{ overflowX: 'auto' }}>
                <table className="table">
                  <thead>
                    <tr>
                      <th>Model</th>
                      <th style={{ textAlign: 'right' }}>Events</th>
                      <th style={{ textAlign: 'right' }}>Tokens</th>
                      <th style={{ textAlign: 'right' }}>Cost</th>
                      <th style={{ minWidth: 180 }}>Share</th>
                    </tr>
                  </thead>
                  <tbody>
                    {modelRows.map(([model, row], idx) => {
                      const denom =
                        sortKey === 'cost'
                          ? totalCost
                          : sortKey === 'events'
                            ? eventCount
                            : totalTokens;
                      const metric =
                        sortKey === 'cost'
                          ? row.cost_usd
                          : sortKey === 'events'
                            ? row.events
                            : row.tokens;
                      const pct = denom > 0 ? (metric / denom) * 100 : 0;
                      const accent = ACCENTS[idx % ACCENTS.length];
                      return (
                        <tr key={model}>
                          <td className="mono" style={{ color: 'var(--ink-100)' }}>
                            <span
                              className={`badge`}
                              style={{
                                color: `var(--accent-${accent})`,
                                borderColor: `color-mix(in srgb, var(--accent-${accent}) 35%, transparent)`,
                                background: `color-mix(in srgb, var(--accent-${accent}) 8%, transparent)`,
                                marginRight: 8,
                              }}
                            >
                              #{idx + 1}
                            </span>
                            {model}
                          </td>
                          <td className="mono" style={{ textAlign: 'right' }}>
                            {formatNumber(row.events)}
                          </td>
                          <td className="mono" style={{ textAlign: 'right' }}>
                            {formatNumber(row.tokens)}
                          </td>
                          <td className="mono" style={{ textAlign: 'right' }}>
                            {formatCost(row.cost_usd)}
                          </td>
                          <td>
                            <div
                              style={{ display: 'flex', alignItems: 'center', gap: 10 }}
                              title={`${pct.toFixed(1)}% of ${SORT_LABELS[sortKey].toLowerCase()}`}
                            >
                              <div className="progress-track" style={{ flex: 1 }}>
                                <div
                                  className="progress-bar"
                                  style={{
                                    width: `${Math.min(100, pct).toFixed(2)}%`,
                                    background: `linear-gradient(90deg, var(--accent-${accent}), var(--accent-cyan))`,
                                  }}
                                />
                              </div>
                              <span
                                className="mono"
                                style={{
                                  color: 'var(--ink-300)',
                                  minWidth: 48,
                                  textAlign: 'right',
                                }}
                              >
                                {pct.toFixed(1)}%
                              </span>
                            </div>
                          </td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              </div>
            )}
          </Panel>

          <Panel
            title="Prometheus exposition"
            tag={prometheus.data?.available ? 'live' : 'fallback'}
          >
            <div
              style={{
                display: 'flex',
                flexWrap: 'wrap',
                alignItems: 'center',
                gap: 10,
                marginBottom: 12,
              }}
            >
              <span className={`badge ${prometheus.data?.available ? 'ok' : 'warn'}`}>
                {prometheus.data?.available ? 'client installed' : 'client missing'}
              </span>
              <span className="muted" style={{ fontSize: 12 }}>
                {prometheus.data?.available
                  ? 'Exporting Baselithbot counters, gauges, and histograms.'
                  : 'prometheus_client not installed — fallback payload shown.'}
              </span>
              <div style={{ marginLeft: 'auto', display: 'flex', gap: 6 }}>
                <button
                  className="btn xs ghost"
                  onClick={() => setPromExpanded((v) => !v)}
                  disabled={!prometheus.data}
                >
                  <Icon path={paths.terminal} size={14} />
                  {promExpanded ? 'Collapse' : 'Expand'}
                </button>
                <button
                  className="btn xs ghost"
                  onClick={copyProm}
                  disabled={!prometheus.data?.text}
                >
                  <Icon path={paths.copy} size={14} />
                  Copy
                </button>
              </div>
            </div>
            {!prometheus.data ? (
              <EmptyState
                title="Metrics unavailable"
                description="Prometheus exposition could not be loaded from the dashboard passthrough."
              />
            ) : (
              <pre
                className="code-block"
                style={{
                  maxHeight: promExpanded ? 720 : 240,
                  overflow: 'auto',
                  transition: 'max-height var(--t-med) ease',
                }}
              >
                {truncate(prometheus.data.text, promExpanded ? 200_000 : 4000) || '# empty payload'}
              </pre>
            )}
          </Panel>
        </>
      )}
    </div>
  );
}

function ModelDistribution({
  rows,
  total,
  sortKey,
}: {
  rows: Array<[string, { events: number; tokens: number; cost_usd: number }]>;
  total: number;
  sortKey: SortKey;
}) {
  if (rows.length === 0) {
    return (
      <EmptyState
        title="Empty distribution"
        description="Once events are recorded, the top models will appear here."
      />
    );
  }
  return (
    <ul
      style={{
        listStyle: 'none',
        padding: 0,
        margin: 0,
        display: 'flex',
        flexDirection: 'column',
        gap: 10,
      }}
    >
      {rows.map(([model, row], idx) => {
        const metric =
          sortKey === 'cost' ? row.cost_usd : sortKey === 'events' ? row.events : row.tokens;
        const pct = total > 0 ? (metric / total) * 100 : 0;
        const accent = ACCENTS[idx % ACCENTS.length];
        const display =
          sortKey === 'cost'
            ? formatCost(row.cost_usd)
            : formatNumber(sortKey === 'events' ? row.events : row.tokens);
        return (
          <li key={model} style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
            <div
              style={{
                display: 'flex',
                justifyContent: 'space-between',
                alignItems: 'baseline',
                gap: 8,
              }}
            >
              <span
                className="mono"
                style={{
                  color: 'var(--ink-100)',
                  fontSize: 12,
                  overflow: 'hidden',
                  textOverflow: 'ellipsis',
                  whiteSpace: 'nowrap',
                }}
                title={model}
              >
                {model}
              </span>
              <span className="mono" style={{ color: `var(--accent-${accent})`, fontSize: 12 }}>
                {display}
              </span>
            </div>
            <div className="progress-track">
              <div
                className="progress-bar"
                style={{
                  width: `${Math.min(100, pct).toFixed(2)}%`,
                  background: `linear-gradient(90deg, var(--accent-${accent}), var(--accent-cyan))`,
                }}
              />
            </div>
          </li>
        );
      })}
    </ul>
  );
}

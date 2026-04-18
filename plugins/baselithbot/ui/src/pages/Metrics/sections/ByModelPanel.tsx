import { Panel } from '../../../components/Panel';
import { EmptyState } from '../../../components/EmptyState';
import { formatCost, formatNumber } from '../../../lib/format';
import { ACCENTS, SORT_LABELS, type ModelRow, type SortKey } from '../helpers';

interface Props {
  modelRows: ModelRow[];
  filter: string;
  setFilter: (v: string) => void;
  sortKey: SortKey;
  setSortKey: (k: SortKey) => void;
  totalTokens: number;
  totalCost: number;
  eventCount: number;
}

export function ByModelPanel({
  modelRows,
  filter,
  setFilter,
  sortKey,
  setSortKey,
  totalTokens,
  totalCost,
  eventCount,
}: Props) {
  return (
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
  );
}

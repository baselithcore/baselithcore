import { EmptyState } from '../../components/EmptyState';
import { formatCost, formatNumber } from '../../lib/format';
import { ACCENTS, type ModelRow, type SortKey } from './helpers';

export function ModelDistribution({
  rows,
  total,
  sortKey,
}: {
  rows: ModelRow[];
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

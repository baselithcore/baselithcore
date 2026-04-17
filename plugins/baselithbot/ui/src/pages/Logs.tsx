import { useDeferredValue, useMemo, useState } from 'react';
import { useDashboardEvents } from '../lib/sse';
import { PageHeader } from '../components/PageHeader';
import { Panel } from '../components/Panel';
import { EmptyState } from '../components/EmptyState';
import { formatRelative, truncate } from '../lib/format';

export function Logs() {
  const { events, state } = useDashboardEvents(500);
  const [filter, setFilter] = useState('');
  const [typeFilter, setTypeFilter] = useState<string>('');
  const deferredFilter = useDeferredValue(filter);

  const uniqueTypes = useMemo(() => {
    const s = new Set<string>();
    for (const e of events) s.add(e.type);
    return Array.from(s).sort();
  }, [events]);

  const filtered = useMemo(() => {
    const needle = deferredFilter.trim().toLowerCase();
    return events
      .slice()
      .reverse()
      .filter((e) => {
        if (typeFilter && e.type !== typeFilter) return false;
        if (!needle) return true;
        return (
          e.type.toLowerCase().includes(needle) ||
          JSON.stringify(e.payload).toLowerCase().includes(needle)
        );
      });
  }, [deferredFilter, events, typeFilter]);

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 24 }}>
      <PageHeader
        eyebrow="Observability"
        title="Live event stream"
        description="Server-Sent Events from the dashboard event bus. Filter by type or substring."
        actions={
          <span className={`pill ${state === 'open' ? 'ok' : state === 'error' ? 'down' : 'warn'}`}>
            <span className="dot" />
            <span>sse {state}</span>
          </span>
        }
      />

      <Panel>
        <div className="inline" style={{ marginBottom: 12 }}>
          <input
            className="input"
            placeholder="Search type or payload…"
            value={filter}
            onChange={(e) => setFilter(e.target.value)}
            style={{ flex: 1, minWidth: 220 }}
          />
          <select
            className="select"
            style={{ maxWidth: 220 }}
            value={typeFilter}
            onChange={(e) => setTypeFilter(e.target.value)}
          >
            <option value="">all types ({uniqueTypes.length})</option>
            {uniqueTypes.map((t) => (
              <option key={t} value={t}>
                {t}
              </option>
            ))}
          </select>
        </div>

        {filtered.length === 0 ? (
          <EmptyState
            title="No events"
            description="Events will appear here as dashboard actions fire."
          />
        ) : (
          <div
            style={{
              display: 'flex',
              flexDirection: 'column',
              gap: 6,
              maxHeight: 560,
              overflowY: 'auto',
            }}
          >
            {filtered.map((ev, i) => (
              <div
                key={`${ev.ts}-${i}`}
                style={{
                  border: '1px solid var(--panel-border)',
                  borderRadius: 'var(--radius-md)',
                  background: 'rgba(15,19,25,0.5)',
                  padding: '10px 12px',
                }}
              >
                <div
                  className="mono"
                  style={{
                    display: 'flex',
                    justifyContent: 'space-between',
                    fontSize: 11,
                    color: 'var(--ink-400)',
                  }}
                >
                  <span style={{ color: 'var(--accent-teal)' }}>{ev.type}</span>
                  <span>{formatRelative(ev.ts)}</span>
                </div>
                <div
                  className="mono"
                  style={{
                    fontSize: 11,
                    color: 'var(--ink-200)',
                    marginTop: 4,
                    whiteSpace: 'pre-wrap',
                    wordBreak: 'break-all',
                  }}
                >
                  {truncate(JSON.stringify(ev.payload, null, 2), 2000)}
                </div>
              </div>
            ))}
          </div>
        )}
      </Panel>
    </div>
  );
}

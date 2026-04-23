import { Panel } from '../../../components/Panel';
import { EmptyState } from '../../../components/EmptyState';
import { Icon, paths } from '../../../lib/icons';
import { truncate } from '../../../lib/format';

interface Props {
  data: { available: boolean; text: string } | undefined;
  promExpanded: boolean;
  setPromExpanded: (updater: (v: boolean) => boolean) => void;
  copyProm: () => void;
}

export function PrometheusPanel({ data, promExpanded, setPromExpanded, copyProm }: Props) {
  return (
    <Panel title="Prometheus exposition" tag={data?.available ? 'live' : 'fallback'}>
      <div
        style={{
          display: 'flex',
          flexWrap: 'wrap',
          alignItems: 'center',
          gap: 10,
          marginBottom: 12,
        }}
      >
        <span className={`badge ${data?.available ? 'ok' : 'warn'}`}>
          {data?.available ? 'client installed' : 'client missing'}
        </span>
        <span className="muted" style={{ fontSize: 12 }}>
          {data?.available
            ? 'Exporting Baselithbot counters, gauges, and histograms.'
            : 'prometheus_client not installed — fallback payload shown.'}
        </span>
        <div style={{ marginLeft: 'auto', display: 'flex', gap: 6 }}>
          <button
            className="btn xs ghost"
            onClick={() => setPromExpanded((v) => !v)}
            disabled={!data}
          >
            <Icon path={paths.terminal} size={14} />
            {promExpanded ? 'Collapse' : 'Expand'}
          </button>
          <button className="btn xs ghost" onClick={copyProm} disabled={!data?.text}>
            <Icon path={paths.copy} size={14} />
            Copy
          </button>
        </div>
      </div>
      {!data ? (
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
          {truncate(data.text, promExpanded ? 200_000 : 4000) || '# empty payload'}
        </pre>
      )}
    </Panel>
  );
}

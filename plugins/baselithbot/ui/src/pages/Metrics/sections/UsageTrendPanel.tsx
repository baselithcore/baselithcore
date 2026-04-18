import { Line } from 'react-chartjs-2';
import { Panel } from '../../../components/Panel';
import { EmptyState } from '../../../components/EmptyState';
import { ModelDistribution } from '../components';
import type { ModelRow, SortKey } from '../helpers';

interface UsageEvent {
  total_tokens: number;
  latency_ms: number;
}

interface Props {
  events: UsageEvent[];
  chartData: {
    labels: string[];
    datasets: Array<{
      label: string;
      data: number[];
      borderColor: string;
      backgroundColor: string;
      pointRadius: number;
      tension: number;
      fill: boolean;
      yAxisID: string;
    }>;
  };
  modelRows: ModelRow[];
  distributionTotal: number;
  sortKey: SortKey;
}

export function UsageTrendPanel({
  events,
  chartData,
  modelRows,
  distributionTotal,
  sortKey,
}: Props) {
  return (
    <section className="grid grid-split-2-1">
      <Panel title="Usage trend" tag="tokens · latency">
        {events.length === 0 ? (
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
          total={distributionTotal}
          sortKey={sortKey}
        />
      </Panel>
    </section>
  );
}

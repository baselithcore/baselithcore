import { EmptyState } from '../../../components/EmptyState';
import { Panel } from '../../../components/Panel';
import type { RunTaskState } from '../../../lib/api';
import { ExtractedDataView } from '../components';

export function HistoryExtracted({ selectedRun }: { selectedRun: RunTaskState }) {
  return (
    <section className="grid grid-split-2-1">
      <Panel title="History" tag={`${selectedRun.history.length}`}>
        {selectedRun.history.length === 0 ? (
          <EmptyState
            title="No history yet"
            description="The Observe → Plan → Act trace will appear here as soon as the run starts executing."
          />
        ) : (
          <div className="trace">
            {selectedRun.history.map((step, idx) => (
              <div key={`${selectedRun.run_id}-${idx}`} className="step">
                <span className="num">#{idx + 1}</span>
                <span className="text">{step}</span>
              </div>
            ))}
          </div>
        )}
      </Panel>

      <Panel
        title="Extracted data"
        tag={Object.keys(selectedRun.extracted_data).length.toString()}
      >
        {Object.keys(selectedRun.extracted_data).length === 0 ? (
          <EmptyState
            title="No extracted data"
            description="Any EXTRACT actions will accumulate their partial results here."
          />
        ) : (
          <ExtractedDataView data={selectedRun.extracted_data} />
        )}
      </Panel>
    </section>
  );
}

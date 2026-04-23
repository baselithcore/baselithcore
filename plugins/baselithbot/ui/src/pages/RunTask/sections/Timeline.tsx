import { EmptyState } from '../../../components/EmptyState';
import { Panel } from '../../../components/Panel';
import { formatRelative } from '../../../lib/format';
import { describeRunEvent } from '../helpers';

interface TimelineProps {
  selectedRunId: string | null;
  runEvents: { type: string; payload: Record<string, unknown>; ts: number }[];
  selectedRunPresent: boolean;
}

export function Timeline({ selectedRunId, runEvents, selectedRunPresent }: TimelineProps) {
  return (
    <Panel title="Execution timeline" tag={selectedRunPresent ? `${runEvents.length} events` : ''}>
      {!selectedRunId ? (
        <EmptyState
          title="No run selected"
          description="Select a run to inspect the live execution timeline emitted on the dashboard event bus."
        />
      ) : runEvents.length === 0 ? (
        <EmptyState
          title="Waiting for run events"
          description="Step-level run events will appear here as the task progresses."
        />
      ) : (
        <div className="trace">
          {runEvents.map((event, index) => (
            <div key={`${event.ts}-${index}`} className="step">
              <span className="num">{event.type.replace('run.', '')}</span>
              <div style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
                <span className="text">{describeRunEvent(event)}</span>
                <span className="muted mono" style={{ fontSize: 11 }}>
                  {formatRelative(event.ts)}
                </span>
              </div>
            </div>
          ))}
        </div>
      )}
    </Panel>
  );
}

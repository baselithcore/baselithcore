import { Panel } from '../../../components/Panel';
import type { ApprovalRequest } from '../../../lib/api';
import { formatNumber } from '../../../lib/format';
import { HistoryRow } from '../components';

interface HistoryListProps {
  history: ApprovalRequest[];
  onSelect: (id: string) => void;
}

export function HistoryList({ history, onSelect }: HistoryListProps) {
  return (
    <Panel title="Recent decisions" tag={`${formatNumber(history.length)} rows`}>
      {history.length === 0 ? (
        <div className="info-block">No approval history captured yet.</div>
      ) : (
        <div className="stack-list">
          {history.map((req) => (
            <HistoryRow key={req.id} req={req} onSelect={() => onSelect(req.id)} />
          ))}
        </div>
      )}
    </Panel>
  );
}

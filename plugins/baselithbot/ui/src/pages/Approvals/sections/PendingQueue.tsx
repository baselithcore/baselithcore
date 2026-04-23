import { EmptyState } from '../../../components/EmptyState';
import { Panel } from '../../../components/Panel';
import type { ApprovalPolicy, ApprovalRequest } from '../../../lib/api';
import { formatNumber } from '../../../lib/format';
import { PendingRequestCard } from '../components';
import type { DecisionInput } from '../helpers';

interface PendingQueueProps {
  policy: ApprovalPolicy;
  pending: ApprovalRequest[];
  now: number;
  reasonDrafts: Record<string, string>;
  onReasonChange: (id: string, value: string) => void;
  onDecision: (input: DecisionInput) => void;
  onSelect: (id: string) => void;
  isPending: boolean;
  activeMutationId: string | null;
  activeMutationVerdict: DecisionInput['verdict'] | null;
  mutationLabel: string;
}

export function PendingQueue({
  policy,
  pending,
  now,
  reasonDrafts,
  onReasonChange,
  onDecision,
  onSelect,
  isPending,
  activeMutationId,
  activeMutationVerdict,
  mutationLabel,
}: PendingQueueProps) {
  if (!policy.enabled) {
    return (
      <EmptyState
        title="Approval gate disabled"
        description="`ComputerUseConfig.require_approval_for` is empty for the currently enabled surfaces, so privileged actions will not queue here."
      />
    );
  }

  if (pending.length === 0) {
    return (
      <EmptyState
        title="No pending approvals"
        description="The gate is armed, but no live Computer Use request is currently waiting for operator input."
      />
    );
  }

  return (
    <Panel title="Pending queue" tag={`${formatNumber(pending.length)} live requests`}>
      <div className="approval-request-list">
        {pending.map((req) => {
          const activeVerdict =
            activeMutationId === req.id ? (activeMutationVerdict ?? null) : null;
          return (
            <PendingRequestCard
              key={req.id}
              req={req}
              now={now}
              reason={reasonDrafts[req.id] ?? ''}
              busy={isPending}
              activeVerdict={activeVerdict}
              onReasonChange={(value) => onReasonChange(req.id, value)}
              onApprove={() =>
                onDecision({
                  id: req.id,
                  verdict: 'approve',
                  reason: reasonDrafts[req.id] ?? '',
                })
              }
              onDeny={() =>
                onDecision({
                  id: req.id,
                  verdict: 'deny',
                  reason: reasonDrafts[req.id] ?? '',
                })
              }
              onSelect={() => onSelect(req.id)}
            />
          );
        })}
      </div>

      {isPending ? <div className="approvals-inline-note">{mutationLabel}</div> : null}
    </Panel>
  );
}

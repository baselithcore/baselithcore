import { useEffect, useMemo, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { PageHeader } from '../../components/PageHeader';
import { Skeleton } from '../../components/Skeleton';
import { useToasts } from '../../components/ToastProvider';
import { api } from '../../lib/api';
import { Icon, paths } from '../../lib/icons';
import type { DecisionInput } from './helpers';
import { topEntries } from './helpers';
import { HeroPanel } from './sections/HeroPanel';
import { HistoryList } from './sections/HistoryList';
import { PendingQueue } from './sections/PendingQueue';
import { PolicyPanels } from './sections/PolicyPanels';
import { RequestDrawer } from './sections/RequestDrawer';
import { StatGrid } from './sections/StatGrid';

export function Approvals() {
  const qc = useQueryClient();
  const { push } = useToasts();

  const [selectedRequestId, setSelectedRequestId] = useState<string | null>(null);
  const [reasonDrafts, setReasonDrafts] = useState<Record<string, string>>({});
  const [now, setNow] = useState(() => Date.now() / 1000);

  useEffect(() => {
    const intervalId = window.setInterval(() => setNow(Date.now() / 1000), 500);
    return () => window.clearInterval(intervalId);
  }, []);

  const { data, isLoading, isFetching, refetch } = useQuery({
    queryKey: ['approvals'],
    queryFn: api.approvals,
    refetchInterval: 2_500,
  });

  const decisionMutation = useMutation({
    mutationFn: ({ id, verdict, reason }: DecisionInput) =>
      verdict === 'approve'
        ? api.approveRequest(id, reason || undefined)
        : api.denyRequest(id, reason || undefined),
    onSuccess: (_result, variables) => {
      setReasonDrafts((prev) => {
        const next = { ...prev };
        delete next[variables.id];
        return next;
      });
      push({
        tone: 'success',
        title: variables.verdict === 'approve' ? 'Request approved' : 'Request denied',
        description: variables.id,
      });
      qc.invalidateQueries({ queryKey: ['approvals'] });
    },
    onError: (error: unknown) =>
      push({
        tone: 'error',
        title: 'Decision failed',
        description: error instanceof Error ? error.message : String(error),
      }),
  });

  const pending = data?.pending ?? [];
  const history = useMemo(() => (data?.history ?? []).slice().reverse(), [data]);
  const allRequests = useMemo(() => [...pending, ...(data?.history ?? [])], [data, pending]);
  const selectedRequest = allRequests.find((request) => request.id === selectedRequestId) ?? null;

  useEffect(() => {
    if (!selectedRequestId) return;
    if (!allRequests.some((request) => request.id === selectedRequestId)) {
      setSelectedRequestId(null);
    }
  }, [allRequests, selectedRequestId]);

  const policy = data?.policy;
  const statusCounts = data?.status_counts ?? {};
  const pendingCount = pending.length;
  const approvedCount = statusCounts.approved ?? 0;
  const deniedCount = statusCounts.denied ?? 0;
  const timedOutCount = statusCounts.timed_out ?? 0;
  const topCapabilities = useMemo(
    () => topEntries(data?.capability_counts ?? {}, 5),
    [data?.capability_counts]
  );
  const topActions = useMemo(() => topEntries(data?.action_counts ?? {}, 5), [data?.action_counts]);
  const activeMutationId = decisionMutation.variables?.id ?? null;

  const mutationLabel =
    decisionMutation.variables?.verdict === 'deny' ? 'Denying request…' : 'Approving request…';

  if (isLoading || !data || !policy) {
    return (
      <div className="approvals-page">
        <PageHeader
          eyebrow="Human Gate"
          title="Approvals"
          description="Operator decisions for privileged Computer Use actions routed through Baselithbot's human-in-the-loop gate."
        />
        <Skeleton height={320} />
      </div>
    );
  }

  return (
    <div className="approvals-page">
      <PageHeader
        eyebrow="Human Gate"
        title="Approvals"
        description="Approval queue for privileged Computer Use requests. Decisions here unblock or reject live shell, filesystem, mouse, keyboard, and screenshot actions."
        actions={
          <div className="inline">
            <button
              type="button"
              className="btn ghost"
              disabled={isFetching}
              onClick={() => refetch()}
            >
              <Icon path={paths.refresh} size={14} />
              {isFetching ? 'Refreshing…' : 'Refresh'}
            </button>
          </div>
        }
      />

      <HeroPanel
        data={data}
        policy={policy}
        now={now}
        pendingCount={pendingCount}
        approvedCount={approvedCount}
        deniedCount={deniedCount}
        timedOutCount={timedOutCount}
      />

      <StatGrid
        pendingCount={pendingCount}
        approvedCount={approvedCount}
        deniedCount={deniedCount}
        timedOutCount={timedOutCount}
      />

      <PolicyPanels policy={policy} topCapabilities={topCapabilities} topActions={topActions} />

      <PendingQueue
        policy={policy}
        pending={pending}
        now={now}
        reasonDrafts={reasonDrafts}
        onReasonChange={(id, value) =>
          setReasonDrafts((prev) => ({ ...prev, [id]: value }))
        }
        onDecision={(input) => decisionMutation.mutate(input)}
        onSelect={(id) => setSelectedRequestId(id)}
        isPending={decisionMutation.isPending}
        activeMutationId={activeMutationId}
        activeMutationVerdict={decisionMutation.variables?.verdict ?? null}
        mutationLabel={mutationLabel}
      />

      <HistoryList history={history} onSelect={(id) => setSelectedRequestId(id)} />

      <RequestDrawer
        selectedRequest={selectedRequest}
        now={now}
        onClose={() => setSelectedRequestId(null)}
      />
    </div>
  );
}

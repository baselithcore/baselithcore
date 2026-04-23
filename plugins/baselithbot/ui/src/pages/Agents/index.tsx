import { useMemo, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { api, type AgentInfo, type CustomAgentPayload } from '../../lib/api';
import { PageHeader } from '../../components/PageHeader';
import { Panel } from '../../components/Panel';
import { EmptyState } from '../../components/EmptyState';
import { Skeleton } from '../../components/Skeleton';
import { useToasts } from '../../components/ToastProvider';
import { formatNumber } from '../../lib/format';
import { dispatchResultStatus } from './helpers';
import { CustomAgentForm } from './sections/CustomAgentForm';
import { AgentRoster } from './sections/AgentRoster';
import { AgentDetails } from './sections/AgentDetails';

export function Agents() {
  const qc = useQueryClient();
  const { push } = useToasts();
  const [selected, setSelected] = useState<string | null>(null);
  const [formOpen, setFormOpen] = useState(false);
  const [dispatchQuery, setDispatchQuery] = useState('');
  const [dispatchResult, setDispatchResult] = useState<string | null>(null);

  const { data, isLoading } = useQuery({
    queryKey: ['agents'],
    queryFn: api.agents,
    refetchInterval: 15_000,
  });

  const { data: catalog } = useQuery({
    queryKey: ['agents', 'catalog'],
    queryFn: api.agentsCatalog,
    staleTime: 60_000,
  });

  const agents = useMemo(() => data?.agents ?? [], [data]);
  const active = useMemo(
    () => agents.find((agent) => agent.name === selected) ?? agents[0] ?? null,
    [agents, selected]
  );
  const totals = data?.totals ?? { all: 0, custom: 0, system: 0 };

  const invalidate = () => qc.invalidateQueries({ queryKey: ['agents'] });

  const create = useMutation({
    mutationFn: (payload: CustomAgentPayload) => api.createCustomAgent(payload),
    onSuccess: (_, payload) => {
      invalidate();
      setFormOpen(false);
      push({
        tone: 'success',
        title: 'Custom agent registered',
        description: `${payload.name} added to the registry.`,
      });
    },
    onError: (err: unknown) =>
      push({
        tone: 'error',
        title: 'Custom agent create failed',
        description: err instanceof Error ? err.message : String(err),
      }),
  });

  const remove = useMutation({
    mutationFn: (name: string) => api.deleteCustomAgent(name),
    onSuccess: (_, name) => {
      setSelected((cur) => (cur === name ? null : cur));
      invalidate();
      push({
        tone: 'success',
        title: 'Agent removed',
        description: `${name} was deleted from the registry.`,
      });
    },
    onError: (err: unknown) =>
      push({
        tone: 'error',
        title: 'Agent removal failed',
        description: err instanceof Error ? err.message : String(err),
      }),
  });

  const dispatch = useMutation({
    mutationFn: ({ name, query }: { name: string; query: string }) =>
      api.dispatchAgent(name, query),
    onSuccess: (res) => {
      setDispatchResult(JSON.stringify(res, null, 2));
      push({
        tone: 'success',
        title: 'Agent dispatched',
        description: `Result status: ${dispatchResultStatus(res.result)}.`,
      });
    },
    onError: (err: unknown) =>
      push({
        tone: 'error',
        title: 'Agent dispatch failed',
        description: err instanceof Error ? err.message : String(err),
      }),
  });

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 24 }}>
      <PageHeader
        eyebrow="Routing"
        title="Registered agents"
        description={`${formatNumber(totals.all)} agents (${formatNumber(totals.system)} system · ${formatNumber(totals.custom)} custom). Inspect keywords, priority, metadata, and dispatch test queries.`}
      />

      <Panel>
        <div className="toolbar">
          <div className="muted" style={{ fontSize: 12 }}>
            Custom agents are persisted under the <span className="mono">custom.</span> prefix and
            survive restarts.
          </div>
          <button
            type="button"
            className="btn"
            style={{ marginLeft: 'auto' }}
            onClick={() => setFormOpen((open) => !open)}
          >
            {formOpen ? 'Close form' : 'New custom agent'}
          </button>
        </div>

        {formOpen && catalog && (
          <CustomAgentForm
            actions={catalog.actions}
            namePrefix={catalog.name_prefix}
            submitting={create.isPending}
            onSubmit={(payload) => create.mutate(payload)}
            onCancel={() => setFormOpen(false)}
          />
        )}
      </Panel>

      {isLoading && <Skeleton height={260} />}

      {!isLoading && agents.length === 0 && (
        <EmptyState
          title="No agents registered"
          description="System agents register at plugin boot. Use 'New custom agent' to add your own."
        />
      )}

      {agents.length > 0 && (
        <section className="grid grid-split-1-2">
          <AgentRoster
            agents={agents}
            active={active}
            onSelect={(name) => {
              setSelected(name);
              setDispatchResult(null);
            }}
          />

          <AgentDetails
            active={active}
            dispatchQuery={dispatchQuery}
            dispatchResult={dispatchResult}
            dispatchPending={dispatch.isPending}
            onDispatchQueryChange={setDispatchQuery}
            onDispatch={(name, query) => dispatch.mutate({ name, query })}
            removePending={remove.isPending}
            onRemove={(name) => remove.mutate(name)}
          />
        </section>
      )}
    </div>
  );
}

export type { AgentInfo };

import { useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { api } from '../lib/api';
import { PageHeader } from '../components/PageHeader';
import { Panel } from '../components/Panel';
import { EmptyState } from '../components/EmptyState';
import { Skeleton } from '../components/Skeleton';
import { formatNumber, truncate } from '../lib/format';

export function Agents() {
  const [selected, setSelected] = useState<string | null>(null);

  const { data, isLoading } = useQuery({
    queryKey: ['agents'],
    queryFn: api.agents,
    refetchInterval: 15_000,
  });

  const agents = data?.agents ?? [];
  const active = agents.find((agent) => agent.name === selected) ?? agents[0] ?? null;

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 24 }}>
      <PageHeader
        eyebrow="Routing"
        title="Registered agents"
        description={`${formatNumber(agents.length)} agents exposed by the registry. Inspect keywords, priority, and per-agent metadata.`}
      />

      {isLoading && <Skeleton height={260} />}

      {!isLoading && agents.length === 0 && (
        <EmptyState
          title="No agents registered"
          description="Agent registry entries will appear here once the plugin boots with colocated agents."
        />
      )}

      {agents.length > 0 && (
        <section className="grid grid-split-1-2">
          <Panel title="Roster" tag={`${agents.length}`}>
            <div className="stack-list">
              {agents.map((agent) => {
                const isActive = agent.name === active?.name;
                return (
                  <button
                    key={agent.name}
                    type="button"
                    className={`select-row ${isActive ? 'active' : ''}`}
                    onClick={() => setSelected(agent.name)}
                  >
                    <div className="select-row-head">
                      <span className="mono" style={{ color: 'var(--ink-100)' }}>
                        {agent.name}
                      </span>
                      <span className="badge">p{agent.priority}</span>
                    </div>
                    <div className="muted" style={{ fontSize: 12 }}>
                      {truncate(agent.description || 'No description provided.', 110)}
                    </div>
                  </button>
                );
              })}
            </div>
          </Panel>

          <Panel
            title={active ? active.name : 'Agent details'}
            tag={active ? `${active.keywords.length} keywords` : ''}
          >
            {active ? (
              <div style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>
                <div className="detail-grid">
                  <MetaTile label="Priority" value={String(active.priority)} />
                  <MetaTile label="Keywords" value={String(active.keywords.length)} />
                  <MetaTile
                    label="Metadata keys"
                    value={String(Object.keys(active.metadata).length)}
                  />
                </div>

                <section>
                  <div className="section-label">Description</div>
                  <div className="info-block">
                    {active.description || 'No description provided.'}
                  </div>
                </section>

                <section>
                  <div className="section-label">Keywords</div>
                  {active.keywords.length === 0 ? (
                    <div className="info-block muted">No keywords configured.</div>
                  ) : (
                    <div className="chip-row">
                      {active.keywords.map((keyword) => (
                        <span key={keyword} className="badge">
                          {keyword}
                        </span>
                      ))}
                    </div>
                  )}
                </section>

                <section>
                  <div className="section-label">Metadata</div>
                  <pre className="code-block">{JSON.stringify(active.metadata, null, 2)}</pre>
                </section>
              </div>
            ) : null}
          </Panel>
        </section>
      )}
    </div>
  );
}

function MetaTile({ label, value }: { label: string; value: string }) {
  return (
    <div className="meta-tile">
      <span className="meta-label">{label}</span>
      <span className="mono" style={{ color: 'var(--ink-100)' }}>
        {value}
      </span>
    </div>
  );
}

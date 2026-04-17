import { useQuery } from '@tanstack/react-query';
import { api } from '../lib/api';
import { PageHeader } from '../components/PageHeader';
import { Panel } from '../components/Panel';
import { EmptyState } from '../components/EmptyState';
import { Skeleton } from '../components/Skeleton';
import { formatAbsolute, formatNumber } from '../lib/format';

export function Workspaces() {
  const { data, isLoading } = useQuery({
    queryKey: ['workspaces'],
    queryFn: api.workspaces,
    refetchInterval: 15_000,
  });

  const workspaces = data?.workspaces ?? [];
  const totalOverrides = workspaces.reduce(
    (sum, workspace) => sum + workspace.channels_overridden.length,
    0
  );

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 24 }}>
      <PageHeader
        eyebrow="Isolation"
        title="Workspaces"
        description={`${formatNumber(workspaces.length)} configured workspaces · ${formatNumber(totalOverrides)} channel overrides bound to runtime state.`}
      />

      {isLoading && <Skeleton height={260} />}

      {!isLoading && workspaces.length === 0 && (
        <EmptyState
          title="No workspaces configured"
          description="Workspace runtime summaries will appear here once the plugin provisions isolated state buckets."
        />
      )}

      {workspaces.length > 0 && (
        <section className="grid grid-cols-2">
          {workspaces.map((workspace) => (
            <Panel
              key={workspace.name}
              title={workspace.name}
              tag={workspace.primary ? 'primary' : 'secondary'}
            >
              <div style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>
                <div className="detail-grid">
                  <MetaTile label="Created" value={formatAbsolute(workspace.created_at)} />
                  <MetaTile
                    label="Overrides"
                    value={String(workspace.channels_overridden.length)}
                  />
                </div>

                <section>
                  <div className="section-label">Runtime flags</div>
                  <div className="chip-row">
                    <span className={`badge ${workspace.primary ? 'ok' : 'muted'}`}>
                      {workspace.primary ? 'primary' : 'isolated'}
                    </span>
                    <span className="badge">
                      {workspace.channels_overridden.length > 0
                        ? 'channel overrides'
                        : 'default channels'}
                    </span>
                  </div>
                </section>

                <section>
                  <div className="section-label">Channel overrides</div>
                  {workspace.channels_overridden.length === 0 ? (
                    <div className="info-block muted">
                      No channel-specific overrides configured.
                    </div>
                  ) : (
                    <div className="chip-row">
                      {workspace.channels_overridden.map((channel) => (
                        <span key={channel} className="badge">
                          {channel}
                        </span>
                      ))}
                    </div>
                  )}
                </section>
              </div>
            </Panel>
          ))}
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

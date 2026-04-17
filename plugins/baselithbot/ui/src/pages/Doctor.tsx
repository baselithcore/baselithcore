import { useQuery } from '@tanstack/react-query';
import { api, type DoctorPathInfo, type DoctorPluginRuntime } from '../lib/api';
import { PageHeader } from '../components/PageHeader';
import { Panel } from '../components/Panel';
import { Skeleton } from '../components/Skeleton';
import { Icon, paths } from '../lib/icons';

export function Doctor() {
  const { data, isLoading, refetch, isFetching } = useQuery({
    queryKey: ['doctor'],
    queryFn: api.doctor,
    refetchInterval: 30_000,
  });

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 24 }}>
      <PageHeader
        eyebrow="Diagnostics"
        title="Doctor"
        description="Environment, dependency, and live plugin state probes."
        actions={
          <button type="button" className="btn sm" onClick={() => refetch()} disabled={isFetching}>
            <span className={isFetching ? 'spin' : ''}>
              <Icon path={paths.refresh} size={12} />
            </span>
            Re-run
          </button>
        }
      />

      {isLoading && <Skeleton height={240} />}

      {data && (
        <>
          <section className="grid grid-cols-3">
            <Panel title="Platform">
              <Grid entries={Object.entries(data.platform)} />
            </Panel>
            <Panel title="Python dependencies">
              <BoolList entries={data.python_dependencies} />
            </Panel>
            <Panel title="System binaries">
              <BoolList entries={data.system_binaries} />
            </Panel>
          </section>

          {data.plugin_runtime && (
            <section className="grid grid-cols-2">
              <Panel title="Agent">
                <Grid
                  entries={[
                    ['state', data.plugin_runtime.agent.state],
                    ['backend_started', String(data.plugin_runtime.agent.backend_started)],
                    ['stealth_enabled', String(data.plugin_runtime.agent.stealth_enabled)],
                  ]}
                />
              </Panel>
              <Panel title="Cron scheduler">
                <Grid
                  entries={[
                    ['backend', data.plugin_runtime.cron.backend],
                    ['running', String(data.plugin_runtime.cron.running)],
                    ['jobs', String(data.plugin_runtime.cron.jobs)],
                    ['custom_jobs', String(data.plugin_runtime.cron.custom_jobs)],
                  ]}
                />
              </Panel>
              <Panel title="Registries">
                <Grid entries={registryRows(data.plugin_runtime)} />
              </Panel>
              <Panel title="Usage & inbound">
                <Grid
                  entries={[
                    ...Object.entries(data.plugin_runtime.usage).map(
                      ([k, v]) => [k, String(v)] as [string, string]
                    ),
                    ...Object.entries(data.plugin_runtime.inbound).map(
                      ([k, v]) => [`inbound.${k}`, String(v)] as [string, string]
                    ),
                  ]}
                />
              </Panel>
            </section>
          )}

          {data.state_paths && (
            <section>
              <Panel title="State paths">
                <PathList entries={data.state_paths} />
              </Panel>
            </section>
          )}
        </>
      )}
    </div>
  );
}

function registryRows(rt: DoctorPluginRuntime): [string, string][] {
  return [
    ['channels.known', String(rt.channels.known)],
    ['channels.live', String(rt.channels.live)],
    ['sessions', String(rt.sessions.count)],
    ['skills', String(rt.skills.count)],
    ['workspaces', String(rt.workspaces.count)],
    ['agents.system', String(rt.agents.system)],
    ['agents.custom', String(rt.agents.custom)],
    ['provider_keys.configured', `${rt.provider_keys.configured}/${rt.provider_keys.total}`],
    ['nodes.paired', String(rt.nodes.paired)],
    ['canvas.widgets', String(rt.canvas.widgets)],
  ];
}

function Grid({ entries }: { entries: [string, string][] }) {
  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
      {entries.map(([k, v]) => (
        <div
          key={k}
          style={{
            display: 'flex',
            justifyContent: 'space-between',
            padding: '8px 12px',
            borderRadius: 'var(--radius-sm)',
            border: '1px solid var(--panel-border)',
            background: 'rgba(15,19,25,0.4)',
            fontSize: 12,
          }}
        >
          <span className="muted">{k}</span>
          <span className="mono">{String(v)}</span>
        </div>
      ))}
    </div>
  );
}

function BoolList({ entries }: { entries: Record<string, boolean> }) {
  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
      {Object.entries(entries).map(([k, v]) => (
        <div
          key={k}
          style={{
            display: 'flex',
            justifyContent: 'space-between',
            alignItems: 'center',
            padding: '8px 12px',
            borderRadius: 'var(--radius-sm)',
            border: '1px solid var(--panel-border)',
            background: 'rgba(15,19,25,0.4)',
            fontSize: 12,
          }}
        >
          <span className="mono">{k}</span>
          <span className={`badge ${v ? 'ok' : 'muted'}`}>{v ? 'available' : 'missing'}</span>
        </div>
      ))}
    </div>
  );
}

function PathList({ entries }: { entries: Record<string, DoctorPathInfo> }) {
  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
      {Object.entries(entries).map(([label, info]) => (
        <div
          key={label}
          style={{
            display: 'grid',
            gridTemplateColumns: '160px 1fr auto',
            gap: 12,
            alignItems: 'center',
            padding: '8px 12px',
            borderRadius: 'var(--radius-sm)',
            border: '1px solid var(--panel-border)',
            background: 'rgba(15,19,25,0.4)',
            fontSize: 12,
          }}
        >
          <span className="muted">{label}</span>
          <span className="mono" style={{ overflow: 'hidden', textOverflow: 'ellipsis' }}>
            {info.path}
          </span>
          <span className={`badge ${info.exists ? 'ok' : 'muted'}`}>
            {info.exists ? info.kind : 'missing'}
            {info.writable === false ? ' (read-only)' : ''}
          </span>
        </div>
      ))}
    </div>
  );
}

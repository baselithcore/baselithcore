import { useMemo } from 'react';
import { useQuery } from '@tanstack/react-query';
import { api } from '../lib/api';
import { PageHeader } from '../components/PageHeader';
import { Panel } from '../components/Panel';
import { Skeleton } from '../components/Skeleton';
import { EmptyState } from '../components/EmptyState';
import { formatNumber } from '../lib/format';

export function Channels() {
  const { data, isLoading } = useQuery({
    queryKey: ['channels'],
    queryFn: api.channels,
    refetchInterval: 10_000,
  });

  const { liveCount, totalEvents } = useMemo(() => {
    const list = data?.channels ?? [];
    return {
      liveCount: list.filter((c) => c.live).length,
      totalEvents: list.reduce((a, c) => a + c.inbound_events, 0),
    };
  }, [data]);

  if (isLoading || !data) return <Skeleton height={320} />;

  if (data.channels.length === 0)
    return (
      <>
        <PageHeader
          eyebrow="Transports"
          title="Channels"
          description="Messaging channels registered with Baselithbot."
        />
        <EmptyState
          title="No channels registered"
          description="Configure and bootstrap channels in the plugin configuration."
        />
      </>
    );

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 24 }}>
      <PageHeader
        eyebrow="Transports"
        title="Channels"
        description={`${formatNumber(data.channels.length)} registered · ${formatNumber(liveCount)} live · ${formatNumber(totalEvents)} inbound events`}
      />

      <Panel padded={false}>
        <table className="table">
          <thead>
            <tr>
              <th style={{ width: 220 }}>Channel</th>
              <th>Status</th>
              <th>Inbound events</th>
            </tr>
          </thead>
          <tbody>
            {data.channels.map((c) => (
              <tr key={c.name}>
                <td>
                  <span className="mono" style={{ color: 'var(--ink-100)' }}>
                    {c.name}
                  </span>
                </td>
                <td>
                  {c.live ? (
                    <span className="badge ok">live</span>
                  ) : (
                    <span className="badge muted">registered</span>
                  )}
                </td>
                <td>
                  <span className="mono">{formatNumber(c.inbound_events)}</span>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </Panel>
    </div>
  );
}

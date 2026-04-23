import { useDeferredValue, useMemo, useState } from 'react';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { api, type Channel } from '../../lib/api';
import { DetailDrawer } from '../../components/DetailDrawer';
import { PageHeader } from '../../components/PageHeader';
import { Skeleton } from '../../components/Skeleton';
import { EmptyState } from '../../components/EmptyState';
import { useToasts } from '../../components/ToastProvider';
import { useConfirm } from '../../components/ConfirmProvider';
import { type SortKey, type StatusFilter } from './helpers';
import { ChannelStats } from './sections/ChannelStats';
import { ChannelRegistry } from './sections/ChannelRegistry';
import { ChannelEditor } from './sections/ChannelEditor';

export function Channels() {
  const [search, setSearch] = useState('');
  const [status, setStatus] = useState<StatusFilter>('all');
  const [sort, setSort] = useState<SortKey>('events');
  const [selected, setSelected] = useState<Channel | null>(null);
  const deferredSearch = useDeferredValue(search);
  const { push } = useToasts();
  const confirm = useConfirm();
  const qc = useQueryClient();

  const { data, isLoading } = useQuery({
    queryKey: ['channels'],
    queryFn: api.channels,
    refetchInterval: 10_000,
  });

  const channels = data?.channels ?? [];
  const { liveCount, configuredCount, totalEvents, missingCount } = useMemo(
    () => ({
      liveCount: channels.filter((c) => c.live).length,
      configuredCount: channels.filter((c) => c.configured).length,
      totalEvents: channels.reduce((sum, c) => sum + c.inbound_events, 0),
      missingCount: channels.filter((c) => !c.configured).length,
    }),
    [channels]
  );

  const filtered = useMemo(() => {
    const needle = deferredSearch.trim().toLowerCase();
    return channels
      .filter((channel) => {
        if (status === 'live' && !channel.live) return false;
        if (status === 'configured' && !channel.configured) return false;
        if (status === 'missing' && channel.configured) return false;
        if (!needle) return true;
        return channel.name.toLowerCase().includes(needle);
      })
      .sort((left, right) => {
        if (sort === 'name') return left.name.localeCompare(right.name);
        if (sort === 'status') {
          const rank = (c: Channel) => (c.live ? 0 : c.configured ? 1 : 2);
          return rank(left) - rank(right) || left.name.localeCompare(right.name);
        }
        return right.inbound_events - left.inbound_events || left.name.localeCompare(right.name);
      });
  }, [channels, deferredSearch, sort, status]);

  if (isLoading || !data) return <Skeleton height={320} />;

  if (channels.length === 0)
    return (
      <>
        <PageHeader
          eyebrow="Transports"
          title="Channels"
          description="Review transport readiness, complete missing credentials, and launch adapters from one operational surface."
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
        description="Operational view of every registered channel, with readiness, traffic, and credential health at a glance."
      />

      <ChannelStats
        total={channels.length}
        configuredCount={configuredCount}
        missingCount={missingCount}
        totalEvents={totalEvents}
        liveCount={liveCount}
      />

      <ChannelRegistry
        filtered={filtered}
        search={search}
        onSearchChange={setSearch}
        status={status}
        onStatusChange={setStatus}
        sort={sort}
        onSortChange={setSort}
        onSelect={setSelected}
      />

      <DetailDrawer
        open={!!selected}
        title={selected?.name ?? ''}
        subtitle="Channel details"
        onClose={() => setSelected(null)}
      >
        {selected && (
          <ChannelEditor
            channel={selected}
            onSaved={() => qc.invalidateQueries({ queryKey: ['channels'] })}
            onClose={() => setSelected(null)}
            notify={push}
            confirm={confirm}
          />
        )}
      </DetailDrawer>
    </div>
  );
}

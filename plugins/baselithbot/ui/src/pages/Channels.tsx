import { useDeferredValue, useMemo, useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { api, type Channel } from '../lib/api';
import { DetailDrawer } from '../components/DetailDrawer';
import { PageHeader } from '../components/PageHeader';
import { Panel } from '../components/Panel';
import { Skeleton } from '../components/Skeleton';
import { EmptyState } from '../components/EmptyState';
import { formatNumber } from '../lib/format';

type StatusFilter = 'all' | 'live' | 'registered';
type SortKey = 'events' | 'name';

export function Channels() {
  const [search, setSearch] = useState('');
  const [status, setStatus] = useState<StatusFilter>('all');
  const [sort, setSort] = useState<SortKey>('events');
  const [selected, setSelected] = useState<Channel | null>(null);
  const deferredSearch = useDeferredValue(search);

  const { data, isLoading } = useQuery({
    queryKey: ['channels'],
    queryFn: api.channels,
    refetchInterval: 10_000,
  });

  const channels = data?.channels ?? [];
  const { liveCount, totalEvents } = useMemo(() => {
    return {
      liveCount: channels.filter((channel) => channel.live).length,
      totalEvents: channels.reduce((sum, channel) => sum + channel.inbound_events, 0),
    };
  }, [channels]);

  const filtered = useMemo(() => {
    const needle = deferredSearch.trim().toLowerCase();
    return channels
      .filter((channel) => {
        if (status === 'live' && !channel.live) return false;
        if (status === 'registered' && channel.live) return false;
        if (!needle) return true;
        return channel.name.toLowerCase().includes(needle);
      })
      .sort((left, right) => {
        if (sort === 'name') return left.name.localeCompare(right.name);
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
        description={`${formatNumber(channels.length)} registered · ${formatNumber(liveCount)} live · ${formatNumber(totalEvents)} inbound events`}
      />

      <Panel>
        <div className="toolbar">
          <input
            className="input toolbar-grow"
            placeholder="Filter channels…"
            value={search}
            onChange={(event) => setSearch(event.target.value)}
          />
          <select
            className="select"
            value={status}
            onChange={(event) => setStatus(event.target.value as StatusFilter)}
          >
            <option value="all">all statuses</option>
            <option value="live">live only</option>
            <option value="registered">registered only</option>
          </select>
          <select
            className="select"
            value={sort}
            onChange={(event) => setSort(event.target.value as SortKey)}
          >
            <option value="events">sort: inbound events</option>
            <option value="name">sort: name</option>
          </select>
        </div>

        {filtered.length === 0 ? (
          <EmptyState
            title="No channels match"
            description="Adjust the search or status filter to inspect another registered transport."
          />
        ) : (
          <div className="cards-grid">
            {filtered.map((channel) => (
              <button
                key={channel.name}
                type="button"
                className="record-card"
                onClick={() => setSelected(channel)}
              >
                <div className="record-card-head">
                  <div className="record-card-title mono">{channel.name}</div>
                  <span className={`badge ${channel.live ? 'ok' : 'muted'}`}>
                    {channel.live ? 'live' : 'registered'}
                  </span>
                </div>
                <div className="record-card-meta">
                  <div className="record-kv">
                    <span>Inbound events</span>
                    <span className="mono">{formatNumber(channel.inbound_events)}</span>
                  </div>
                  <div className="record-kv">
                    <span>Status</span>
                    <span>{channel.live ? 'Active transport' : 'Configured only'}</span>
                  </div>
                </div>
              </button>
            ))}
          </div>
        )}
      </Panel>

      <DetailDrawer
        open={!!selected}
        title={selected?.name ?? ''}
        subtitle="Channel details"
        onClose={() => setSelected(null)}
      >
        {selected && (
          <>
            <div className="detail-grid">
              <MetaTile label="Status" value={selected.live ? 'live' : 'registered'} />
              <MetaTile label="Inbound events" value={formatNumber(selected.inbound_events)} />
            </div>
            <div className="stack-section">
              <div className="section-label">Operational summary</div>
              <div className="info-block">
                {selected.live
                  ? 'This channel is currently active and receiving or capable of receiving inbound traffic.'
                  : 'This channel is registered in the plugin but is not currently marked as live.'}
              </div>
            </div>
          </>
        )}
      </DetailDrawer>
    </div>
  );
}

function MetaTile({ label, value }: { label: string; value: string }) {
  return (
    <div className="meta-tile">
      <span className="meta-label">{label}</span>
      <span>{value}</span>
    </div>
  );
}

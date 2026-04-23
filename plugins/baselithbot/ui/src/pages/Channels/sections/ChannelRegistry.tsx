import type { Channel } from '../../../lib/api';
import { EmptyState } from '../../../components/EmptyState';
import { Panel } from '../../../components/Panel';
import { formatNumber, formatRelative } from '../../../lib/format';
import { statusAccent, statusLabel, type SortKey, type StatusFilter } from '../helpers';

interface ChannelRegistryProps {
  filtered: Channel[];
  search: string;
  onSearchChange: (value: string) => void;
  status: StatusFilter;
  onStatusChange: (value: StatusFilter) => void;
  sort: SortKey;
  onSortChange: (value: SortKey) => void;
  onSelect: (channel: Channel) => void;
}

export function ChannelRegistry({
  filtered,
  search,
  onSearchChange,
  status,
  onStatusChange,
  sort,
  onSortChange,
  onSelect,
}: ChannelRegistryProps) {
  return (
    <Panel
      title="Channel Registry"
      tag={`${formatNumber(filtered.length)} visible`}
      className="channels-panel"
    >
      <div className="channel-toolbar">
        <input
          className="input channel-toolbar-search"
          placeholder="Search by channel name"
          value={search}
          onChange={(event) => onSearchChange(event.target.value)}
        />
        <div className="channel-toolbar-controls">
          <select
            className="select"
            value={status}
            onChange={(event) => onStatusChange(event.target.value as StatusFilter)}
          >
            <option value="all">all statuses</option>
            <option value="live">live only</option>
            <option value="configured">configured</option>
            <option value="missing">missing config</option>
          </select>
          <select
            className="select"
            value={sort}
            onChange={(event) => onSortChange(event.target.value as SortKey)}
          >
            <option value="events">sort: inbound events</option>
            <option value="name">sort: name</option>
            <option value="status">sort: status</option>
          </select>
        </div>
      </div>

      {filtered.length === 0 ? (
        <EmptyState
          title="No channels match"
          description="Adjust the search or status filter to inspect another registered transport."
        />
      ) : (
        <div className="cards-grid channels-grid">
          {filtered.map((channel) => {
            const info = statusLabel(channel);
            const accent = statusAccent(channel);
            const previewFields = channel.missing_fields.slice(0, 3);
            const hiddenFields = Math.max(0, channel.missing_fields.length - previewFields.length);
            const updatedLabel = channel.updated_at ? formatRelative(channel.updated_at) : 'never';
            return (
              <button
                key={channel.name}
                type="button"
                className={`record-card channel-card ${accent}`}
                onClick={() => onSelect(channel)}
              >
                <div className="channel-card-headline">
                  <div className="channel-card-heading">
                    <div className="record-card-title mono">{channel.name}</div>
                    <div className="channel-card-sub">
                      {channel.required_fields.length > 0
                        ? `${channel.required_fields.length} required field${channel.required_fields.length === 1 ? '' : 's'}`
                        : 'No required credentials'}
                    </div>
                  </div>
                  <span className={`badge ${info.tone}`}>{info.label}</span>
                </div>
                <div className="channel-card-stats">
                  <div className="channel-mini-stat">
                    <span>Inbound events</span>
                    <strong className="mono">{formatNumber(channel.inbound_events)}</strong>
                  </div>
                  <div className="channel-mini-stat">
                    <span>Last config update</span>
                    <strong>{updatedLabel}</strong>
                  </div>
                </div>
                <div className="channel-card-body">
                  {channel.configured ? (
                    <div className="channel-card-ready">
                      <span className="channel-card-ready-dot" aria-hidden />
                      All required credentials are saved
                    </div>
                  ) : (
                    <>
                      <div className="channel-card-missing-title">Missing credentials</div>
                      <div className="chip-row">
                        {previewFields.map((field) => (
                          <span key={field} className="badge muted mono">
                            {field}
                          </span>
                        ))}
                        {hiddenFields > 0 && (
                          <span className="badge muted">+{hiddenFields} more</span>
                        )}
                      </div>
                    </>
                  )}
                </div>
                <div className="channel-card-foot">
                  <span>
                    {channel.live
                      ? 'Open controls, test outbound delivery, or stop the adapter'
                      : channel.configured
                        ? 'Review configuration and start the adapter'
                        : 'Complete the missing credentials to activate this channel'}
                  </span>
                  <span className="channel-card-arrow">Open</span>
                </div>
              </button>
            );
          })}
        </div>
      )}
    </Panel>
  );
}

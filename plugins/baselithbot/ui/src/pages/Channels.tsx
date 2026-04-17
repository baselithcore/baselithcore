import { useDeferredValue, useEffect, useMemo, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { api, ApiError, type Channel } from '../lib/api';
import { DetailDrawer } from '../components/DetailDrawer';
import { PageHeader } from '../components/PageHeader';
import { Panel } from '../components/Panel';
import { Skeleton } from '../components/Skeleton';
import { EmptyState } from '../components/EmptyState';
import { StatCard } from '../components/StatCard';
import { useToasts } from '../components/ToastProvider';
import { useConfirm } from '../components/ConfirmProvider';
import { formatNumber, formatRelative } from '../lib/format';
import { paths } from '../lib/icons';

type StatusFilter = 'all' | 'live' | 'configured' | 'missing';
type SortKey = 'events' | 'name' | 'status';

function statusLabel(channel: Channel): { label: string; tone: 'ok' | 'muted' | 'warn' } {
  if (channel.live) return { label: 'live', tone: 'ok' };
  if (channel.enabled && !channel.configured) return { label: 'needs config', tone: 'warn' };
  if (channel.configured) return { label: 'configured', tone: 'muted' };
  return { label: 'registered', tone: 'muted' };
}

function statusAccent(channel: Channel): 'live' | 'configured' | 'missing' {
  if (channel.live) return 'live';
  if (channel.configured) return 'configured';
  return 'missing';
}

function isSensitiveField(name: string): boolean {
  const lower = name.toLowerCase();
  return (
    lower.endsWith('token') ||
    lower.endsWith('key') ||
    lower.endsWith('password') ||
    lower.endsWith('secret') ||
    lower === 'private_key_hex'
  );
}

const FIELD_PLACEHOLDERS: Record<string, string> = {
  webhook_url: 'https://hooks.slack.com/services/… or provider webhook URL',
  gateway_url: 'https://gateway.example.com/hooks/messages',
  server_url: 'https://server.example.com',
  rpc_url: 'http://127.0.0.1:8080/api/v1/rpc',
  relay_url: 'wss://relay.example.com',
  homeserver: 'https://matrix.example.org',
  server: 'irc.libera.chat',
  username: 'bot-user or service account',
  password: 'channel password or app password',
  nick: 'baselithbot',
  oauth_token: 'oauth:xxxxxxxxxxxxxxxx',
  bot_token: '1234567890:AAExampleTelegramBotToken',
  channel_access_token: 'LINE channel access token',
  access_token: 'provider access token',
  private_key_hex: '64-character hex private key',
  public_key_hex: '64-character hex public key',
  from_number: '+393331234567',
  phone_number_id: 'WhatsApp phone number id',
  room_id: '!ops:example.org',
  default_channel: '#ops-alerts',
  api_version: 'v19.0',
};

function titleCaseField(name: string): string {
  return name.replace(/_/g, ' ');
}

function placeholderForField(
  channelName: string,
  fieldName: string,
  currentValue: string | number | boolean | undefined
): string {
  const current = currentValue === undefined ? '' : String(currentValue);
  const lower = fieldName.toLowerCase();

  const channelSpecific: Record<string, Record<string, string>> = {
    slack: { webhook_url: 'https://hooks.slack.com/services/T…/B…/…' },
    discord: { webhook_url: 'https://discord.com/api/webhooks/.../...' },
    microsoft_teams: { webhook_url: 'https://outlook.office.com/webhook/…' },
    google_chat: { webhook_url: 'https://chat.googleapis.com/v1/spaces/.../messages?key=…' },
    telegram: { bot_token: '1234567890:AAExampleTelegramBotToken' },
    whatsapp: {
      access_token: 'Meta WhatsApp Cloud API access token',
      phone_number_id: '123456789012345',
    },
    matrix: {
      homeserver: 'https://matrix.example.org',
      access_token: 'Matrix access token',
      room_id: '!ops:example.org',
    },
    signal: {
      rpc_url: 'http://127.0.0.1:8080/api/v1/rpc',
      from_number: '+393331234567',
    },
    irc: {
      server: 'irc.libera.chat',
      nick: 'baselithbot',
    },
    nostr: {
      relay_url: 'wss://relay.example.com',
      private_key_hex: '64-character hex private key',
      public_key_hex: '64-character hex public key',
    },
    nextcloud_talk: {
      server_url: 'https://cloud.example.com',
      username: 'bot-user',
      password: 'app password',
    },
    bluebubbles: {
      server_url: 'https://bluebubbles.example.com',
      password: 'BlueBubbles password',
    },
    twitch: {
      oauth_token: 'oauth:xxxxxxxxxxxxxxxx',
      nick: 'your_twitch_bot',
    },
  };

  const specific = channelSpecific[channelName]?.[fieldName];
  const generic = FIELD_PLACEHOLDERS[fieldName];
  const base =
    specific ??
    generic ??
    (lower.endsWith('_url')
      ? 'https://example.com/...'
      : lower.endsWith('_token') || lower.endsWith('_key')
        ? `Paste ${titleCaseField(fieldName)}`
        : lower.includes('number')
          ? '+393331234567'
          : `Enter ${titleCaseField(fieldName)}`);

  if (current) {
    return isSensitiveField(fieldName)
      ? `saved: ${current} · paste a new value only to replace`
      : `current: ${current}`;
  }
  return base;
}

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

      <section className="grid grid-cols-4">
        <StatCard
          label="Registered"
          value={formatNumber(channels.length)}
          sub="available channel adapters"
          iconPath={paths.cable}
          accent="cyan"
        />
        <StatCard
          label="Configured"
          value={formatNumber(configuredCount)}
          sub="ready for start or test"
          iconPath={paths.check}
          accent="teal"
        />
        <StatCard
          label="Needs config"
          value={formatNumber(missingCount)}
          sub="missing required credentials"
          iconPath={paths.shield}
          accent="amber"
        />
        <StatCard
          label="Inbound events"
          value={formatNumber(totalEvents)}
          sub={`${formatNumber(liveCount)} live adapters`}
          iconPath={paths.activity}
          accent="violet"
        />
      </section>

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
            onChange={(event) => setSearch(event.target.value)}
          />
          <div className="channel-toolbar-controls">
            <select
              className="select"
              value={status}
              onChange={(event) => setStatus(event.target.value as StatusFilter)}
            >
              <option value="all">all statuses</option>
              <option value="live">live only</option>
              <option value="configured">configured</option>
              <option value="missing">missing config</option>
            </select>
            <select
              className="select"
              value={sort}
              onChange={(event) => setSort(event.target.value as SortKey)}
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
              const hiddenFields = Math.max(
                0,
                channel.missing_fields.length - previewFields.length
              );
              const updatedLabel = channel.updated_at
                ? formatRelative(channel.updated_at)
                : 'never';
              return (
                <button
                  key={channel.name}
                  type="button"
                  className={`record-card channel-card ${accent}`}
                  onClick={() => setSelected(channel)}
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

interface ChannelEditorProps {
  channel: Channel;
  onSaved: () => void;
  onClose: () => void;
  notify: ReturnType<typeof useToasts>['push'];
  confirm: ReturnType<typeof useConfirm>;
}

function ChannelEditor({ channel, onSaved, onClose, notify, confirm }: ChannelEditorProps) {
  const qc = useQueryClient();
  const detailQuery = useQuery({
    queryKey: ['channels', channel.name, 'config'],
    queryFn: () => api.channelConfig(channel.name),
  });

  const [formValues, setFormValues] = useState<Record<string, string>>({});
  const [unsetFields, setUnsetFields] = useState<string[]>([]);
  const [extraKey, setExtraKey] = useState('');
  const [extraValue, setExtraValue] = useState('');
  const [testTarget, setTestTarget] = useState('');
  const [testText, setTestText] = useState('Baselithbot test message');

  useEffect(() => {
    if (!detailQuery.data) return;
    const seeded: Record<string, string> = {};
    for (const field of detailQuery.data.required_fields) {
      seeded[field] = '';
    }
    for (const [k, v] of Object.entries(detailQuery.data.safe_config)) {
      if (!(k in seeded)) seeded[k] = String(v);
    }
    setFormValues(seeded);
    setUnsetFields([]);
  }, [detailQuery.data]);

  const invalidate = () => {
    qc.invalidateQueries({ queryKey: ['channels'] });
    qc.invalidateQueries({ queryKey: ['channels', channel.name, 'config'] });
    onSaved();
  };

  const handleError = (err: unknown, title: string) => {
    const description =
      err instanceof ApiError
        ? typeof err.body === 'object' && err.body && 'detail' in err.body
          ? JSON.stringify((err.body as { detail: unknown }).detail)
          : err.message
        : err instanceof Error
          ? err.message
          : 'unknown error';
    notify({ title, description, tone: 'error' });
  };

  const saveMutation = useMutation({
    mutationFn: ({ config, unset }: { config: Record<string, string>; unset: string[] }) =>
      api.saveChannelConfig(channel.name, config, unset),
    onSuccess: () => {
      notify({ title: `Saved ${channel.name}`, tone: 'success' });
      invalidate();
    },
    onError: (err) => handleError(err, `Save ${channel.name} failed`),
  });

  const startMutation = useMutation({
    mutationFn: () => api.startChannel(channel.name),
    onSuccess: (res) => {
      notify({
        title: `Started ${channel.name}`,
        description: `adapter: ${res.adapter_status}`,
        tone: 'success',
      });
      invalidate();
    },
    onError: (err) => handleError(err, `Start ${channel.name} failed`),
  });

  const stopMutation = useMutation({
    mutationFn: () => api.stopChannel(channel.name),
    onSuccess: () => {
      notify({ title: `Stopped ${channel.name}`, tone: 'success' });
      invalidate();
    },
    onError: (err) => handleError(err, `Stop ${channel.name} failed`),
  });

  const testMutation = useMutation({
    mutationFn: () => api.testChannel(channel.name, testTarget, testText),
    onSuccess: (res) => {
      const status = (res.result?.status as string | undefined) ?? 'ok';
      notify({
        title: `Test dispatched`,
        description: `status: ${status}`,
        tone: status === 'success' ? 'success' : 'info',
      });
    },
    onError: (err) => handleError(err, `Test ${channel.name} failed`),
  });

  const deleteMutation = useMutation({
    mutationFn: () => api.deleteChannelConfig(channel.name),
    onSuccess: () => {
      notify({ title: `Cleared ${channel.name} config`, tone: 'success' });
      invalidate();
      onClose();
    },
    onError: (err) => handleError(err, `Delete ${channel.name} failed`),
  });

  if (detailQuery.isLoading || !detailQuery.data) {
    return <Skeleton height={200} />;
  }

  const detail = detailQuery.data;

  const addField = () => {
    const key = extraKey.trim();
    if (!key) return;
    setFormValues((prev) => ({ ...prev, [key]: extraValue }));
    setUnsetFields((prev) => prev.filter((field) => field !== key));
    setExtraKey('');
    setExtraValue('');
  };

  const removeField = (key: string) => {
    setUnsetFields((prev) => (prev.includes(key) ? prev : [...prev, key]));
    setFormValues((prev) => {
      const next = { ...prev };
      delete next[key];
      return next;
    });
  };

  const submit = () => {
    const payload: Record<string, string> = {};
    const unset = new Set(unsetFields);
    for (const [k, v] of Object.entries(formValues)) {
      const trimmed = v.trim();
      if (!trimmed) {
        if (!detail.required_fields.includes(k) && detail.safe_config[k] !== undefined) {
          unset.add(k);
        }
        continue;
      }
      payload[k] = trimmed;
    }
    saveMutation.mutate({ config: payload, unset: Array.from(unset) });
  };

  const confirmDelete = async () => {
    const ok = await confirm({
      title: `Remove ${channel.name} config?`,
      description: 'Stored credentials will be erased. The adapter stops if live.',
      confirmLabel: 'Remove',
      tone: 'danger',
    });
    if (ok) deleteMutation.mutate();
  };

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 20 }}>
      <div className="detail-grid">
        <MetaTile
          label="Status"
          value={detail.live ? 'live' : detail.enabled ? 'enabled' : 'idle'}
        />
        <MetaTile label="Inbound events" value={formatNumber(channel.inbound_events)} />
        <MetaTile label="Configured" value={detail.configured ? 'yes' : 'no'} />
        <MetaTile
          label="Missing"
          value={detail.missing_fields.length ? detail.missing_fields.join(', ') : '—'}
        />
      </div>

      <div className="stack-section">
        <div className="section-label">Credentials &amp; config</div>
        <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
          {Object.entries(formValues).map(([key, value]) => {
            const required = detail.required_fields.includes(key);
            const masked = isSensitiveField(key);
            const placeholder = placeholderForField(channel.name, key, detail.safe_config[key]);
            return (
              <div key={key} style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
                <label className="mono" style={{ minWidth: 160, fontSize: 12, opacity: 0.8 }}>
                  {key}
                  {required ? ' *' : ''}
                </label>
                <input
                  className="input"
                  type={masked ? 'password' : 'text'}
                  autoComplete="off"
                  style={{ flex: 1 }}
                  value={value}
                  placeholder={placeholder}
                  onChange={(e) => {
                    const next = e.target.value;
                    setUnsetFields((prev) =>
                      next.trim() ? prev.filter((field) => field !== key) : prev
                    );
                    setFormValues((prev) => ({ ...prev, [key]: next }));
                  }}
                />
                {!required && (
                  <button
                    type="button"
                    className="btn-ghost"
                    onClick={() => removeField(key)}
                    aria-label={`Remove field ${key}`}
                  >
                    ×
                  </button>
                )}
              </div>
            );
          })}

          <div style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
            <input
              className="input"
              placeholder="add optional field key"
              style={{ minWidth: 160, flex: 1 }}
              value={extraKey}
              onChange={(e) => setExtraKey(e.target.value)}
            />
            <input
              className="input"
              placeholder="value"
              style={{ flex: 1 }}
              value={extraValue}
              onChange={(e) => setExtraValue(e.target.value)}
            />
            <button type="button" className="btn" onClick={addField}>
              add
            </button>
          </div>
        </div>

        <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', marginTop: 12 }}>
          <button
            type="button"
            className="btn primary"
            disabled={saveMutation.isPending}
            onClick={submit}
          >
            {saveMutation.isPending ? 'Saving…' : 'Save config'}
          </button>
          {detail.live ? (
            <button
              type="button"
              className="btn"
              disabled={stopMutation.isPending}
              onClick={() => stopMutation.mutate()}
            >
              {stopMutation.isPending ? 'Stopping…' : 'Stop'}
            </button>
          ) : (
            <button
              type="button"
              className="btn"
              disabled={startMutation.isPending || !detail.configured}
              onClick={() => startMutation.mutate()}
              title={detail.configured ? '' : 'Save required fields first'}
            >
              {startMutation.isPending ? 'Starting…' : 'Start'}
            </button>
          )}
          <button
            type="button"
            className="btn-ghost"
            disabled={deleteMutation.isPending || !detail.configured}
            onClick={confirmDelete}
          >
            Remove config
          </button>
        </div>
      </div>

      <div className="stack-section">
        <div className="section-label">Test outbound send</div>
        <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
          <input
            className="input"
            placeholder="target (recipient id, channel id, phone, etc.)"
            value={testTarget}
            onChange={(e) => setTestTarget(e.target.value)}
          />
          <input
            className="input"
            placeholder="message text"
            value={testText}
            onChange={(e) => setTestText(e.target.value)}
          />
          <button
            type="button"
            className="btn"
            disabled={testMutation.isPending || !testTarget.trim() || !detail.configured}
            onClick={() => testMutation.mutate()}
          >
            {testMutation.isPending ? 'Sending…' : 'Send test message'}
          </button>
          {testMutation.data && (
            <pre className="mono" style={{ fontSize: 11, opacity: 0.75, margin: 0 }}>
              {JSON.stringify(testMutation.data.result, null, 2)}
            </pre>
          )}
        </div>
      </div>
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

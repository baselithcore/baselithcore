import { useDeferredValue, useEffect, useMemo, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { api, ApiError, type Channel } from '../lib/api';
import { DetailDrawer } from '../components/DetailDrawer';
import { PageHeader } from '../components/PageHeader';
import { Panel } from '../components/Panel';
import { Skeleton } from '../components/Skeleton';
import { EmptyState } from '../components/EmptyState';
import { useToasts } from '../components/ToastProvider';
import { useConfirm } from '../components/ConfirmProvider';
import { formatNumber } from '../lib/format';

type StatusFilter = 'all' | 'live' | 'configured' | 'missing';
type SortKey = 'events' | 'name' | 'status';

function statusLabel(channel: Channel): { label: string; tone: 'ok' | 'muted' | 'warn' } {
  if (channel.live) return { label: 'live', tone: 'ok' };
  if (channel.enabled && !channel.configured) return { label: 'needs config', tone: 'warn' };
  if (channel.configured) return { label: 'configured', tone: 'muted' };
  return { label: 'registered', tone: 'muted' };
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
  const { liveCount, configuredCount, totalEvents } = useMemo(
    () => ({
      liveCount: channels.filter((c) => c.live).length,
      configuredCount: channels.filter((c) => c.configured).length,
      totalEvents: channels.reduce((sum, c) => sum + c.inbound_events, 0),
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
        description={`${formatNumber(channels.length)} registered · ${formatNumber(configuredCount)} configured · ${formatNumber(liveCount)} live · ${formatNumber(totalEvents)} inbound events`}
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

        {filtered.length === 0 ? (
          <EmptyState
            title="No channels match"
            description="Adjust the search or status filter to inspect another registered transport."
          />
        ) : (
          <div className="cards-grid">
            {filtered.map((channel) => {
              const info = statusLabel(channel);
              return (
                <button
                  key={channel.name}
                  type="button"
                  className="record-card"
                  onClick={() => setSelected(channel)}
                >
                  <div className="record-card-head">
                    <div className="record-card-title mono">{channel.name}</div>
                    <span className={`badge ${info.tone}`}>{info.label}</span>
                  </div>
                  <div className="record-card-meta">
                    <div className="record-kv">
                      <span>Inbound events</span>
                      <span className="mono">{formatNumber(channel.inbound_events)}</span>
                    </div>
                    <div className="record-kv">
                      <span>Config</span>
                      <span>
                        {channel.configured
                          ? 'All required fields set'
                          : `Missing: ${channel.missing_fields.join(', ') || '—'}`}
                      </span>
                    </div>
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
    mutationFn: (payload: Record<string, string>) => api.saveChannelConfig(channel.name, payload),
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
    setExtraKey('');
    setExtraValue('');
  };

  const removeField = (key: string) => {
    setFormValues((prev) => {
      const next = { ...prev };
      delete next[key];
      return next;
    });
  };

  const submit = () => {
    const payload: Record<string, string> = {};
    for (const [k, v] of Object.entries(formValues)) {
      const trimmed = v.trim();
      if (!trimmed) continue;
      payload[k] = trimmed;
    }
    saveMutation.mutate(payload);
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
            const placeholder =
              masked && detail.safe_config[key]
                ? `currently: ${detail.safe_config[key]}`
                : masked
                  ? 'enter secret'
                  : '';
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
                  onChange={(e) => setFormValues((prev) => ({ ...prev, [key]: e.target.value }))}
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

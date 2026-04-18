import { useEffect, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { api, ApiError, type Channel } from '../../../lib/api';
import { Skeleton } from '../../../components/Skeleton';
import { useToasts } from '../../../components/ToastProvider';
import { useConfirm } from '../../../components/ConfirmProvider';
import { formatNumber } from '../../../lib/format';
import { isSensitiveField, placeholderForField } from '../helpers';
import { MetaTile } from '../components';

interface ChannelEditorProps {
  channel: Channel;
  onSaved: () => void;
  onClose: () => void;
  notify: ReturnType<typeof useToasts>['push'];
  confirm: ReturnType<typeof useConfirm>;
}

export function ChannelEditor({ channel, onSaved, onClose, notify, confirm }: ChannelEditorProps) {
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

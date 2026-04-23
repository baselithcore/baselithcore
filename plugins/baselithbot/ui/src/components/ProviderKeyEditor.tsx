import { useMemo, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { api, ApiError, type ProviderKeyEntry } from '../lib/api';
import { useToasts } from './ToastProvider';
import { Icon, paths } from '../lib/icons';

interface Props {
  providers: string[];
  description?: string;
}

interface RowState {
  input: string;
  reveal: boolean;
  busy: 'save' | 'test' | 'clear' | null;
}

const INITIAL_ROW: RowState = { input: '', reveal: false, busy: null };

function formatUpdated(ts: number | null): string {
  if (!ts) return 'never';
  const d = new Date(ts * 1000);
  return d.toLocaleString();
}

export function ProviderKeyEditor({ providers, description }: Props) {
  const qc = useQueryClient();
  const { push } = useToasts();
  const { data, isLoading } = useQuery({
    queryKey: ['provider-keys'],
    queryFn: api.providerKeys,
    refetchInterval: 30_000,
  });

  const [rows, setRows] = useState<Record<string, RowState>>({});

  const configured = useMemo(() => {
    const map: Record<string, ProviderKeyEntry> = {};
    for (const entry of data?.providers ?? []) map[entry.provider] = entry;
    return map;
  }, [data]);

  const updateRow = (provider: string, patch: Partial<RowState>) =>
    setRows((prev) => ({
      ...prev,
      [provider]: { ...INITIAL_ROW, ...(prev[provider] ?? {}), ...patch },
    }));

  const saveMutation = useMutation({
    mutationFn: ({ provider, apiKey }: { provider: string; apiKey: string }) =>
      api.setProviderKey(provider, apiKey),
    onSuccess: (entry) => {
      qc.invalidateQueries({ queryKey: ['provider-keys'] });
      updateRow(entry.provider, { input: '', reveal: false, busy: null });
      push({
        tone: 'success',
        title: `Key saved for ${entry.provider}`,
        description: `Last 4: ${entry.last4 ?? '***'}`,
      });
    },
    onError: (err: unknown, vars) => {
      updateRow(vars.provider, { busy: null });
      const detail =
        err instanceof ApiError ? err.message : err instanceof Error ? err.message : String(err);
      push({ tone: 'error', title: 'Save failed', description: detail });
    },
  });

  const testMutation = useMutation({
    mutationFn: (provider: string) => api.testProviderKey(provider),
    onSuccess: (res) => {
      updateRow(res.provider, { busy: null });
      push({
        tone: res.ok ? 'success' : 'error',
        title: res.ok ? `${res.provider} key works` : `${res.provider} probe failed`,
        description: res.detail,
      });
    },
    onError: (err: unknown, provider) => {
      updateRow(provider, { busy: null });
      const detail = err instanceof Error ? err.message : String(err);
      push({ tone: 'error', title: 'Test failed', description: detail });
    },
  });

  const clearMutation = useMutation({
    mutationFn: (provider: string) => api.deleteProviderKey(provider),
    onSuccess: (res) => {
      qc.invalidateQueries({ queryKey: ['provider-keys'] });
      updateRow(res.provider, { busy: null });
      push({
        tone: res.removed ? 'success' : 'info',
        title: res.removed ? `${res.provider} key removed` : 'No key to remove',
      });
    },
    onError: (err: unknown, provider) => {
      updateRow(provider, { busy: null });
      const detail = err instanceof Error ? err.message : String(err);
      push({ tone: 'error', title: 'Remove failed', description: detail });
    },
  });

  if (isLoading || !data) {
    return <p className="muted">Loading provider keys…</p>;
  }

  const isHttps = typeof window !== 'undefined' && window.location.protocol === 'https:';

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
      {description ? (
        <p className="muted" style={{ margin: 0, fontSize: 12 }}>
          {description}
        </p>
      ) : null}

      {!isHttps ? (
        <div
          className="muted"
          style={{
            fontSize: 12,
            padding: '6px 10px',
            border: '1px solid var(--panel-border)',
            borderRadius: 'var(--radius-md)',
            background: 'rgba(255,180,0,0.08)',
          }}
          role="alert"
        >
          ⚠ Plain HTTP connection — secrets travel unencrypted. Serve the dashboard over HTTPS or
          tunnel through Tailscale before submitting keys.
        </div>
      ) : null}

      {providers.map((provider) => {
        const entry = configured[provider];
        const row = rows[provider] ?? INITIAL_ROW;
        const busy = row.busy;
        return (
          <div
            key={provider}
            className="inline"
            style={{
              alignItems: 'flex-end',
              padding: 10,
              gap: 10,
              border: '1px solid var(--panel-border)',
              borderRadius: 'var(--radius-md)',
              background: 'rgba(15,19,25,0.4)',
              flexWrap: 'wrap',
            }}
          >
            <div style={{ minWidth: 120 }}>
              <div style={{ fontWeight: 600, textTransform: 'capitalize' }}>{provider}</div>
              <div className="muted" style={{ fontSize: 11 }}>
                {entry?.configured
                  ? `set · ${entry.last4 ?? '***'} · ${formatUpdated(entry.updated_at)}`
                  : 'not configured'}
              </div>
            </div>

            <div className="form-row" style={{ flex: 2, minWidth: 220 }}>
              <label htmlFor={`key-${provider}`}>API key</label>
              <input
                id={`key-${provider}`}
                className="input mono"
                type={row.reveal ? 'text' : 'password'}
                autoComplete="off"
                spellCheck={false}
                placeholder={entry?.configured ? 'enter a new value to rotate' : 'sk-…'}
                value={row.input}
                onChange={(e) => updateRow(provider, { input: e.target.value })}
              />
            </div>

            <div className="inline" style={{ gap: 4 }}>
              <button
                type="button"
                className="btn ghost xs"
                disabled={!row.input}
                onClick={() => updateRow(provider, { reveal: !row.reveal })}
                title={row.reveal ? 'Hide' : 'Show'}
              >
                <Icon path={row.reveal ? paths.x : paths.sparkles} size={12} />
                {row.reveal ? 'Hide' : 'Show'}
              </button>
              <button
                type="button"
                className="btn primary xs"
                disabled={!row.input.trim() || busy !== null}
                onClick={() => {
                  updateRow(provider, { busy: 'save' });
                  saveMutation.mutate({ provider, apiKey: row.input.trim() });
                }}
              >
                <Icon path={paths.check} size={12} />
                {busy === 'save' ? 'Saving…' : entry?.configured ? 'Rotate' : 'Save'}
              </button>
              <button
                type="button"
                className="btn xs"
                disabled={!entry?.configured || busy !== null}
                onClick={() => {
                  updateRow(provider, { busy: 'test' });
                  testMutation.mutate(provider);
                }}
              >
                <Icon path={paths.refresh} size={12} />
                {busy === 'test' ? 'Testing…' : 'Test'}
              </button>
              <button
                type="button"
                className="btn danger xs"
                disabled={!entry?.configured || busy !== null}
                onClick={() => {
                  if (!window.confirm(`Remove stored API key for ${provider}?`)) return;
                  updateRow(provider, { busy: 'clear' });
                  clearMutation.mutate(provider);
                }}
              >
                <Icon path={paths.trash} size={12} />
                {busy === 'clear' ? 'Removing…' : 'Remove'}
              </button>
            </div>
          </div>
        );
      })}

      <p className="muted" style={{ fontSize: 11, margin: 0 }}>
        Keys are encrypted at rest with Fernet and never returned by the API. Only the last 4
        characters are echoed back for verification.
      </p>
    </div>
  );
}

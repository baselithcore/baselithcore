import { useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { api } from '../lib/api';
import { PageHeader } from '../components/PageHeader';
import { Panel } from '../components/Panel';
import { StatCard } from '../components/StatCard';
import { EmptyState } from '../components/EmptyState';
import { Skeleton } from '../components/Skeleton';
import { useToasts } from '../components/ToastProvider';
import { Icon, paths } from '../lib/icons';
import { formatAbsolute } from '../lib/format';

export function Nodes() {
  const qc = useQueryClient();
  const { push } = useToasts();
  const [platform, setPlatform] = useState('');
  const [lastToken, setLastToken] = useState<string | null>(null);

  const { data, isLoading } = useQuery({
    queryKey: ['nodes'],
    queryFn: api.nodes,
    refetchInterval: 8_000,
  });

  const issue = useMutation({
    mutationFn: () => api.issueToken(platform || undefined),
    onSuccess: (res) => {
      setLastToken(res.token);
      qc.invalidateQueries({ queryKey: ['nodes'] });
      push({
        tone: 'success',
        title: 'Pairing token issued',
        description: res.platform
          ? `Token generated for ${res.platform}.`
          : 'Token generated for the default platform scope.',
      });
    },
    onError: (err: unknown) =>
      push({
        tone: 'error',
        title: 'Token issue failed',
        description: err instanceof Error ? err.message : String(err),
      }),
  });

  const revoke = useMutation({
    mutationFn: (id: string) => api.revokeNode(id),
    onSuccess: (_, id) => {
      qc.invalidateQueries({ queryKey: ['nodes'] });
      push({
        tone: 'success',
        title: 'Node revoked',
        description: `${id} was removed from paired identities.`,
      });
    },
    onError: (err: unknown) =>
      push({
        tone: 'error',
        title: 'Node revoke failed',
        description: err instanceof Error ? err.message : String(err),
      }),
  });

  const copyToken = async () => {
    if (!lastToken) return;
    try {
      await navigator.clipboard.writeText(lastToken);
      push({
        tone: 'success',
        title: 'Token copied',
        description: 'The latest pairing token is now in the clipboard.',
      });
    } catch {
      push({
        tone: 'error',
        title: 'Clipboard write failed',
        description: 'The browser refused clipboard access for this page.',
      });
    }
  };

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 24 }}>
      <PageHeader
        eyebrow="Mesh"
        title="Paired nodes"
        description="Issue pairing tokens for remote nodes (macOS menu bar, iOS, Android) and revoke paired identities."
      />

      {isLoading && <Skeleton height={120} />}

      {data && (
        <section className="grid grid-cols-3">
          <StatCard
            label="Paired"
            value={data.status.paired}
            iconPath={paths.waypoints}
            accent="teal"
          />
          <StatCard
            label="Pending tokens"
            value={data.status.pending_tokens}
            iconPath={paths.zap}
            accent="amber"
          />
          <StatCard
            label="Token TTL"
            value={`${data.status.ttl_seconds}s`}
            iconPath={paths.clock}
            accent="violet"
          />
        </section>
      )}

      <section className="grid grid-split-1-2">
        <Panel title="Issue pairing token">
          <form
            style={{ display: 'flex', flexDirection: 'column', gap: 12 }}
            onSubmit={(e) => {
              e.preventDefault();
              if (!issue.isPending) issue.mutate();
            }}
          >
            <div className="form-row">
              <label htmlFor="platform">Platform (optional)</label>
              <input
                id="platform"
                className="input"
                placeholder="macos / ios / android"
                value={platform}
                onChange={(e) => setPlatform(e.target.value)}
              />
            </div>
            <button type="submit" className="btn primary" disabled={issue.isPending}>
              <Icon path={paths.plus} size={14} />
              Issue token
            </button>

            {lastToken && (
              <div
                style={{
                  padding: 10,
                  borderRadius: 'var(--radius-md)',
                  border: '1px solid rgba(46,230,196,0.35)',
                  background: 'rgba(46,230,196,0.06)',
                  display: 'flex',
                  flexDirection: 'column',
                  gap: 6,
                }}
              >
                <span
                  style={{
                    fontSize: 10,
                    letterSpacing: '0.2em',
                    textTransform: 'uppercase',
                    color: 'var(--accent-teal)',
                  }}
                >
                  Last token
                </span>
                <span
                  className="mono"
                  style={{
                    fontSize: 11,
                    wordBreak: 'break-all',
                    color: 'var(--ink-100)',
                  }}
                >
                  {lastToken}
                </span>
                <button type="button" className="btn xs ghost" onClick={copyToken}>
                  <Icon path={paths.copy} size={12} />
                  Copy
                </button>
              </div>
            )}
          </form>
        </Panel>

        <Panel title="Paired nodes" tag={`${data?.paired.length ?? 0}`}>
          {data && data.paired.length === 0 ? (
            <EmptyState
              title="No paired nodes"
              description="Nodes that complete the WebSocket handshake will appear here."
            />
          ) : (
            <table className="table">
              <thead>
                <tr>
                  <th>Node ID</th>
                  <th>Platform</th>
                  <th>Paired at</th>
                  <th />
                </tr>
              </thead>
              <tbody>
                {data?.paired.map((n) => (
                  <tr key={n.node_id}>
                    <td className="mono">{n.node_id}</td>
                    <td>
                      <span className="badge">{n.platform}</span>
                    </td>
                    <td className="mono muted">{formatAbsolute(n.paired_at)}</td>
                    <td>
                      <button
                        type="button"
                        className="btn danger xs"
                        disabled={revoke.isPending}
                        onClick={() => {
                          if (!window.confirm(`Revoke paired node "${n.node_id}"?`)) {
                            return;
                          }
                          revoke.mutate(n.node_id);
                        }}
                      >
                        Revoke
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </Panel>
      </section>
    </div>
  );
}

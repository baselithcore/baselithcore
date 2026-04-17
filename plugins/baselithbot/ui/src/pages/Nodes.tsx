import { useDeferredValue, useMemo, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { api } from '../lib/api';
import { useConfirm } from '../components/ConfirmProvider';
import { DetailDrawer } from '../components/DetailDrawer';
import { EmptyState } from '../components/EmptyState';
import { PageHeader } from '../components/PageHeader';
import { Panel } from '../components/Panel';
import { Skeleton } from '../components/Skeleton';
import { StatCard } from '../components/StatCard';
import { useToasts } from '../components/ToastProvider';
import { Icon, paths } from '../lib/icons';
import { formatAbsolute, formatNumber } from '../lib/format';

type SortKey = 'recent' | 'platform' | 'name';

export function Nodes() {
  const qc = useQueryClient();
  const confirm = useConfirm();
  const { push } = useToasts();
  const [issuePlatform, setIssuePlatform] = useState('');
  const [platformFilter, setPlatformFilter] = useState('');
  const [search, setSearch] = useState('');
  const [sort, setSort] = useState<SortKey>('recent');
  const [lastToken, setLastToken] = useState<string | null>(null);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const deferredSearch = useDeferredValue(search);

  const { data, isLoading } = useQuery({
    queryKey: ['nodes'],
    queryFn: api.nodes,
    refetchInterval: 8_000,
  });

  const paired = data?.paired ?? [];
  const selected = useMemo(
    () => paired.find((node) => node.node_id === selectedId) ?? null,
    [paired, selectedId]
  );
  const platforms = useMemo(
    () =>
      Array.from(new Set(paired.map((node) => node.platform))).sort((left, right) =>
        left.localeCompare(right)
      ),
    [paired]
  );

  const filtered = useMemo(() => {
    const needle = deferredSearch.trim().toLowerCase();
    return paired
      .filter((node) => {
        if (platformFilter && node.platform !== platformFilter) return false;
        if (!needle) return true;
        return (
          node.node_id.toLowerCase().includes(needle) ||
          node.platform.toLowerCase().includes(needle)
        );
      })
      .sort((left, right) => {
        if (sort === 'platform') {
          return (
            left.platform.localeCompare(right.platform) || left.node_id.localeCompare(right.node_id)
          );
        }
        if (sort === 'name') return left.node_id.localeCompare(right.node_id);
        return right.paired_at - left.paired_at || left.node_id.localeCompare(right.node_id);
      });
  }, [deferredSearch, paired, platformFilter, sort]);

  const issue = useMutation({
    mutationFn: () => api.issueToken(issuePlatform.trim() || undefined),
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
      setSelectedId((current) => (current === id ? null : current));
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
        description="Issue pairing tokens for remote nodes and inspect paired identities."
      />

      {isLoading && <Skeleton height={120} />}

      {data && (
        <>
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

          <section className="grid grid-split-1-2">
            <Panel title="Issue pairing token">
              <form
                style={{ display: 'flex', flexDirection: 'column', gap: 12 }}
                onSubmit={(event) => {
                  event.preventDefault();
                  if (!issue.isPending) issue.mutate();
                }}
              >
                <div className="form-row">
                  <label htmlFor="platform">Platform (optional)</label>
                  <input
                    id="platform"
                    className="input"
                    placeholder="macos / ios / android"
                    value={issuePlatform}
                    onChange={(event) => setIssuePlatform(event.target.value)}
                  />
                </div>
                <button type="submit" className="btn primary" disabled={issue.isPending}>
                  <Icon path={paths.plus} size={14} />
                  Issue token
                </button>

                {lastToken && (
                  <div className="stack-section">
                    <div className="section-label">Last token</div>
                    <pre className="code-block">{lastToken}</pre>
                    <button type="button" className="btn xs ghost" onClick={copyToken}>
                      <Icon path={paths.copy} size={12} />
                      Copy token
                    </button>
                  </div>
                )}
              </form>
            </Panel>

            <Panel title="Paired nodes" tag={`${formatNumber(paired.length)}`}>
              <div className="toolbar">
                <input
                  className="input toolbar-grow"
                  placeholder="Filter nodes…"
                  value={search}
                  onChange={(event) => setSearch(event.target.value)}
                />
                <select
                  className="select"
                  value={platformFilter}
                  onChange={(event) => setPlatformFilter(event.target.value)}
                >
                  <option value="">all platforms</option>
                  {platforms.map((platform) => (
                    <option key={platform} value={platform}>
                      {platform}
                    </option>
                  ))}
                </select>
                <select
                  className="select"
                  value={sort}
                  onChange={(event) => setSort(event.target.value as SortKey)}
                >
                  <option value="recent">sort: recent</option>
                  <option value="platform">sort: platform</option>
                  <option value="name">sort: node id</option>
                </select>
              </div>

              {paired.length === 0 ? (
                <EmptyState
                  title="No paired nodes"
                  description="Nodes that complete the WebSocket handshake will appear here."
                />
              ) : filtered.length === 0 ? (
                <EmptyState
                  title="No nodes match"
                  description="Adjust the filters to inspect another paired identity."
                />
              ) : (
                <div className="cards-grid">
                  {filtered.map((node) => (
                    <button
                      key={node.node_id}
                      type="button"
                      className="record-card"
                      onClick={() => setSelectedId(node.node_id)}
                    >
                      <div className="record-card-head">
                        <div className="record-card-title mono">{node.node_id}</div>
                        <span className="badge">{node.platform}</span>
                      </div>
                      <div className="record-card-meta">
                        <div className="record-kv">
                          <span>Platform</span>
                          <span>{node.platform}</span>
                        </div>
                        <div className="record-kv">
                          <span>Paired at</span>
                          <span className="mono">{formatAbsolute(node.paired_at)}</span>
                        </div>
                      </div>
                    </button>
                  ))}
                </div>
              )}
            </Panel>
          </section>
        </>
      )}

      <DetailDrawer
        open={!!selected}
        title={selected?.node_id ?? ''}
        subtitle="Paired node details"
        onClose={() => setSelectedId(null)}
      >
        {selected && (
          <>
            <div className="detail-grid">
              <MetaTile label="Node ID" value={selected.node_id} />
              <MetaTile label="Platform" value={selected.platform} />
              <MetaTile label="Paired at" value={formatAbsolute(selected.paired_at)} />
            </div>

            <div className="stack-section">
              <div className="section-label">Operational summary</div>
              <div className="info-block">
                This node has an active paired identity and can reconnect using the current mesh
                credentials.
              </div>
            </div>

            <button
              type="button"
              className="btn danger"
              disabled={revoke.isPending}
              onClick={async () => {
                if (
                  !(await confirm({
                    title: 'Revoke paired node',
                    description: `Node "${selected.node_id}" will lose its paired identity immediately.`,
                    confirmLabel: 'Revoke node',
                    tone: 'danger',
                  }))
                ) {
                  return;
                }
                revoke.mutate(selected.node_id);
              }}
            >
              Revoke node
            </button>
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

import type { AgentInfo } from '../../../lib/api';
import { Panel } from '../../../components/Panel';
import { useConfirm } from '../../../components/ConfirmProvider';
import { Icon, paths } from '../../../lib/icons';
import { MetaTile } from '../components';

interface AgentDetailsProps {
  active: AgentInfo | null;
  dispatchQuery: string;
  dispatchResult: string | null;
  dispatchPending: boolean;
  onDispatchQueryChange: (value: string) => void;
  onDispatch: (name: string, query: string) => void;
  removePending: boolean;
  onRemove: (name: string) => void;
}

export function AgentDetails({
  active,
  dispatchQuery,
  dispatchResult,
  dispatchPending,
  onDispatchQueryChange,
  onDispatch,
  removePending,
  onRemove,
}: AgentDetailsProps) {
  const confirm = useConfirm();

  return (
    <Panel
      title={active ? active.name : 'Agent details'}
      tag={active ? `${active.keywords.length} keywords` : ''}
    >
      {active ? (
        <div style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>
          <div className="detail-grid">
            <MetaTile label="Kind" value={active.kind} />
            <MetaTile label="Priority" value={String(active.priority)} />
            <MetaTile label="Keywords" value={String(active.keywords.length)} />
            <MetaTile label="Metadata keys" value={String(Object.keys(active.metadata).length)} />
          </div>

          <section>
            <div className="section-label">Description</div>
            <div className="info-block">{active.description || 'No description provided.'}</div>
          </section>

          <section>
            <div className="section-label">Keywords</div>
            {active.keywords.length === 0 ? (
              <div className="info-block muted">No keywords configured.</div>
            ) : (
              <div className="chip-row">
                {active.keywords.map((keyword) => (
                  <span key={keyword} className="badge">
                    {keyword}
                  </span>
                ))}
              </div>
            )}
          </section>

          <section>
            <div className="section-label">Metadata</div>
            <pre className="code-block">{JSON.stringify(active.metadata, null, 2)}</pre>
          </section>

          <section>
            <div className="section-label">Dispatch test</div>
            <div className="toolbar" style={{ gap: 8 }}>
              <input
                className="input toolbar-grow"
                placeholder="Query to dispatch…"
                value={dispatchQuery}
                onChange={(event) => onDispatchQueryChange(event.target.value)}
              />
              <button
                type="button"
                className="btn"
                disabled={dispatchPending || !dispatchQuery.trim()}
                onClick={() => onDispatch(active.name, dispatchQuery.trim())}
              >
                {dispatchPending ? 'Dispatching…' : 'Run'}
              </button>
            </div>
            {dispatchResult && (
              <pre className="code-block" style={{ marginTop: 8 }}>
                {dispatchResult}
              </pre>
            )}
          </section>

          {active.custom && (
            <button
              type="button"
              className="btn danger"
              disabled={removePending}
              onClick={async () => {
                if (
                  !(await confirm({
                    title: 'Remove custom agent',
                    description: `"${active.name}" will be deleted from the registry.`,
                    confirmLabel: 'Remove agent',
                    tone: 'danger',
                  }))
                ) {
                  return;
                }
                onRemove(active.name);
              }}
            >
              <Icon path={paths.trash} size={14} />
              Remove agent
            </button>
          )}
        </div>
      ) : null}
    </Panel>
  );
}

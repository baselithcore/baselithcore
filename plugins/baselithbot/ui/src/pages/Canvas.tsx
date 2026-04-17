import { useQuery } from '@tanstack/react-query';
import { api, type CanvasWidget } from '../lib/api';
import { PageHeader } from '../components/PageHeader';
import { Panel } from '../components/Panel';
import { EmptyState } from '../components/EmptyState';
import { Skeleton } from '../components/Skeleton';
import { formatAbsolute, formatNumber, truncate } from '../lib/format';

export function Canvas() {
  const { data, isLoading } = useQuery({
    queryKey: ['canvas'],
    queryFn: api.canvas,
    refetchInterval: 10_000,
  });

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 24 }}>
      <PageHeader
        eyebrow="A2UI"
        title="Canvas snapshot"
        description={`Current in-memory canvas surface with revisioned widgets rendered by Baselithbot.`}
      />

      {isLoading && <Skeleton height={260} />}

      {!isLoading && !data && (
        <EmptyState
          title="Canvas unavailable"
          description="The dashboard could not fetch the current canvas snapshot."
        />
      )}

      {data && (
        <>
          <section className="grid grid-cols-3">
            <MetaPanel title="Surface ID" value={data.surface_id} />
            <MetaPanel title="Revision" value={String(data.revision)} />
            <MetaPanel title="Created" value={formatAbsolute(data.created_at)} />
          </section>

          <section className="grid grid-split-2-1">
            <Panel title="Rendered widgets" tag={`${formatNumber(data.widgets.length)}`}>
              {data.widgets.length === 0 ? (
                <EmptyState
                  title="Canvas is empty"
                  description="Widget primitives appear here once the canvas surface is populated."
                />
              ) : (
                <div className="canvas-stack">
                  {data.widgets.map((widget) => (
                    <CanvasWidgetView key={widget.id} widget={widget} depth={0} />
                  ))}
                </div>
              )}
            </Panel>

            <Panel title="Snapshot JSON" tag="raw">
              <pre className="code-block">{JSON.stringify(data, null, 2)}</pre>
            </Panel>
          </section>
        </>
      )}
    </div>
  );
}

function MetaPanel({ title, value }: { title: string; value: string }) {
  return (
    <Panel title={title}>
      <div className="info-block mono" style={{ color: 'var(--ink-100)' }}>
        {value}
      </div>
    </Panel>
  );
}

function CanvasWidgetView({ widget, depth }: { widget: CanvasWidget; depth: number }) {
  const title =
    widget.type === 'text'
      ? truncate(widget.content, 72)
      : widget.type === 'button'
        ? widget.label
        : widget.type === 'image'
          ? widget.alt || widget.url || widget.id
          : `${widget.ordered ? 'ordered' : 'unordered'} list`;

  return (
    <div className="canvas-widget" style={{ marginLeft: depth === 0 ? 0 : depth * 14 }}>
      <div className="canvas-widget-head">
        <span className="badge">{widget.type}</span>
        <span className="mono muted">{widget.id}</span>
      </div>
      <div className="canvas-widget-title">{title}</div>

      {widget.type === 'text' && <div className="info-block">{widget.content}</div>}

      {widget.type === 'button' && (
        <div className="detail-grid">
          <div className="meta-tile">
            <span className="meta-label">Action</span>
            <span className="mono" style={{ color: 'var(--ink-100)' }}>
              {widget.action}
            </span>
          </div>
          <div className="meta-tile">
            <span className="meta-label">Payload keys</span>
            <span className="mono" style={{ color: 'var(--ink-100)' }}>
              {Object.keys(widget.payload).length}
            </span>
          </div>
        </div>
      )}

      {widget.type === 'image' && (
        <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
          {widget.url && (
            <a href={widget.url} target="_blank" rel="noreferrer" className="mono">
              {truncate(widget.url, 100)}
            </a>
          )}
          {widget.base64_png && (
            <img
              className="screenshot"
              alt={widget.alt || 'Canvas image'}
              src={`data:image/png;base64,${widget.base64_png}`}
            />
          )}
          {!widget.url && !widget.base64_png && (
            <div className="info-block muted">No image payload attached.</div>
          )}
        </div>
      )}

      {widget.type === 'list' && (
        <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
          <div className="info-block">
            {widget.ordered ? 'Ordered list' : 'Unordered list'} with {widget.items.length} child
            widgets.
          </div>
          {widget.items.map((child) => (
            <CanvasWidgetView key={child.id} widget={child} depth={depth + 1} />
          ))}
        </div>
      )}
    </div>
  );
}

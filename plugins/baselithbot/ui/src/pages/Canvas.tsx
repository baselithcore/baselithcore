import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useMemo, useState } from 'react';
import {
  api,
  type CanvasDispatchPayload,
  type CanvasRenderPayload,
  type CanvasWidget,
} from '../lib/api';
import { PageHeader } from '../components/PageHeader';
import { Panel } from '../components/Panel';
import { EmptyState } from '../components/EmptyState';
import { Skeleton } from '../components/Skeleton';
import { useToasts } from '../components/ToastProvider';
import { formatAbsolute, formatNumber, truncate } from '../lib/format';

const SAMPLE_PAYLOAD: CanvasRenderPayload = {
  clear: true,
  widgets: [
    { type: 'text', content: 'Canvas demo — widgets wired end-to-end.' },
    {
      type: 'list',
      ordered: false,
      items: [
        { type: 'text', content: 'Lists now nest recursively.' },
        { type: 'divider', orientation: 'horizontal' },
        { type: 'progress', value: 0.65, label: 'Sample progress 65%' },
      ],
    },
    {
      type: 'button',
      label: 'Ping dispatcher',
      action: 'canvas.sample.ping',
      payload: { source: 'dashboard-demo' },
    },
    {
      type: 'table',
      columns: ['metric', 'value'],
      rows: [
        ['latency_ms', 42],
        ['tokens', 1287],
      ],
      sortable: true,
    },
    {
      type: 'form',
      title: 'Quick note',
      submit_action: 'canvas.sample.submit',
      fields: [
        { name: 'subject', label: 'Subject', type: 'text', required: true },
        { name: 'priority', label: 'Priority', type: 'select', options: ['low', 'high'] },
      ],
    },
    {
      type: 'chart',
      chart_type: 'line',
      x_axis: 'time',
      y_axis: 'tokens',
      series: [{ label: 'tokens', points: [10, 24, 33, 42, 58] }],
    },
  ],
};

export function Canvas() {
  const queryClient = useQueryClient();
  const toasts = useToasts();
  const [lastAction, setLastAction] = useState<string | null>(null);

  const { data, isLoading } = useQuery({
    queryKey: ['canvas'],
    queryFn: api.canvas,
    refetchInterval: 10_000,
  });

  const refresh = () => queryClient.invalidateQueries({ queryKey: ['canvas'] });

  const renderMutation = useMutation({
    mutationFn: (payload: CanvasRenderPayload) => api.canvasRender(payload),
    onSuccess: () => {
      toasts.push({ title: 'Canvas updated', tone: 'success' });
      refresh();
    },
    onError: (err: unknown) =>
      toasts.push({
        title: 'Render failed',
        description: err instanceof Error ? err.message : String(err),
        tone: 'error',
      }),
  });

  const clearMutation = useMutation({
    mutationFn: () => api.canvasClear(),
    onSuccess: () => {
      toasts.push({ title: 'Canvas cleared', tone: 'info' });
      refresh();
    },
    onError: (err: unknown) =>
      toasts.push({
        title: 'Clear failed',
        description: err instanceof Error ? err.message : String(err),
        tone: 'error',
      }),
  });

  const dispatchMutation = useMutation({
    mutationFn: (payload: CanvasDispatchPayload) => api.canvasDispatch(payload),
    onSuccess: (res) => {
      setLastAction(`${res.action} — ${new Date().toLocaleTimeString()}`);
      toasts.push({ title: `Dispatched ${res.action}`, tone: 'success' });
    },
    onError: (err: unknown) =>
      toasts.push({
        title: 'Dispatch failed',
        description: err instanceof Error ? err.message : String(err),
        tone: 'error',
      }),
  });

  const widgetTypes = useMemo(() => {
    if (!data) return {} as Record<string, number>;
    const counts: Record<string, number> = {};
    const walk = (widgets: CanvasWidget[]) => {
      for (const w of widgets) {
        counts[w.type] = (counts[w.type] ?? 0) + 1;
        if (w.type === 'list') walk(w.items);
      }
    };
    walk(data.widgets);
    return counts;
  }, [data]);

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 24 }}>
      <PageHeader
        eyebrow="A2UI"
        title="Canvas snapshot"
        description="Current in-memory canvas surface with revisioned widgets rendered by Baselithbot."
        actions={
          <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}>
            <button
              type="button"
              className="btn"
              onClick={() => renderMutation.mutate(SAMPLE_PAYLOAD)}
              disabled={renderMutation.isPending}
            >
              {renderMutation.isPending ? 'Rendering…' : 'Render demo widgets'}
            </button>
            <button
              type="button"
              className="btn ghost"
              onClick={() => clearMutation.mutate()}
              disabled={clearMutation.isPending}
            >
              {clearMutation.isPending ? 'Clearing…' : 'Clear canvas'}
            </button>
            <button type="button" className="btn ghost" onClick={refresh}>
              Refresh
            </button>
          </div>
        }
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
          <section className="grid grid-cols-4">
            <MetaPanel title="Surface ID" value={data.surface_id} />
            <MetaPanel title="Revision" value={String(data.revision)} />
            <MetaPanel title="Created" value={formatAbsolute(data.created_at)} />
            <MetaPanel
              title="Widget types"
              value={
                Object.keys(widgetTypes).length === 0
                  ? '—'
                  : Object.entries(widgetTypes)
                      .map(([t, n]) => `${t}:${n}`)
                      .join(' · ')
              }
            />
          </section>

          {lastAction && (
            <Panel title="Last dispatched action" tag="event">
              <div className="info-block mono">{lastAction}</div>
            </Panel>
          )}

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
                    <CanvasWidgetView
                      key={widget.id}
                      widget={widget}
                      depth={0}
                      onDispatch={(payload) => dispatchMutation.mutate(payload)}
                      dispatchBusy={dispatchMutation.isPending}
                    />
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

interface CanvasWidgetViewProps {
  widget: CanvasWidget;
  depth: number;
  onDispatch: (payload: CanvasDispatchPayload) => void;
  dispatchBusy: boolean;
}

function CanvasWidgetView({ widget, depth, onDispatch, dispatchBusy }: CanvasWidgetViewProps) {
  const title = widgetTitle(widget);

  return (
    <div className="canvas-widget" style={{ marginLeft: depth === 0 ? 0 : depth * 14 }}>
      <div className="canvas-widget-head">
        <span className="badge">{widget.type}</span>
        <span className="mono muted">{widget.id}</span>
      </div>
      <div className="canvas-widget-title">{title}</div>

      {widget.type === 'text' && <div className="info-block">{widget.content}</div>}

      {widget.type === 'button' && (
        <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
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
          <button
            type="button"
            className="btn"
            disabled={dispatchBusy}
            onClick={() =>
              onDispatch({
                widget_id: widget.id,
                action: widget.action,
                payload: widget.payload,
              })
            }
          >
            {dispatchBusy ? 'Dispatching…' : widget.label}
          </button>
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
            <CanvasWidgetView
              key={child.id}
              widget={child}
              depth={depth + 1}
              onDispatch={onDispatch}
              dispatchBusy={dispatchBusy}
            />
          ))}
        </div>
      )}

      {widget.type === 'form' && (
        <FormPreview
          widget={widget}
          onSubmit={(values) =>
            onDispatch({
              widget_id: widget.id,
              action: widget.submit_action,
              payload: values,
            })
          }
          busy={dispatchBusy}
        />
      )}

      {widget.type === 'table' && (
        <div className="info-block" style={{ overflowX: 'auto' }}>
          <table className="data-table">
            <thead>
              <tr>
                {widget.columns.map((col) => (
                  <th key={col}>{col}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {widget.rows.map((row, rowIdx) => (
                <tr key={rowIdx}>
                  {row.map((cell, cellIdx) => (
                    <td key={cellIdx} className="mono">
                      {cell === null || cell === undefined ? '—' : String(cell)}
                    </td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {widget.type === 'chart' && (
        <div className="detail-grid">
          <div className="meta-tile">
            <span className="meta-label">Chart type</span>
            <span className="mono" style={{ color: 'var(--ink-100)' }}>
              {widget.chart_type}
            </span>
          </div>
          <div className="meta-tile">
            <span className="meta-label">Series</span>
            <span className="mono" style={{ color: 'var(--ink-100)' }}>
              {widget.series.length}
            </span>
          </div>
          <div className="meta-tile">
            <span className="meta-label">Axes</span>
            <span className="mono" style={{ color: 'var(--ink-100)' }}>
              {widget.x_axis || '—'} / {widget.y_axis || '—'}
            </span>
          </div>
        </div>
      )}

      {widget.type === 'progress' && (
        <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
          <div className="progress-track" aria-label={widget.label}>
            <div
              className="progress-bar"
              style={{ width: `${Math.max(0, Math.min(1, widget.value)) * 100}%` }}
            />
          </div>
          <div className="info-block muted">
            {widget.label || `${Math.round(widget.value * 100)}%`}
          </div>
        </div>
      )}

      {widget.type === 'divider' && (
        <div
          className="canvas-divider"
          data-orientation={widget.orientation}
          role="separator"
          aria-orientation={widget.orientation}
        />
      )}
    </div>
  );
}

interface FormPreviewProps {
  widget: Extract<CanvasWidget, { type: 'form' }>;
  onSubmit: (values: Record<string, unknown>) => void;
  busy: boolean;
}

function FormPreview({ widget, onSubmit, busy }: FormPreviewProps) {
  const [values, setValues] = useState<Record<string, unknown>>(() =>
    Object.fromEntries(widget.fields.map((f) => [f.name, f.default ?? '']))
  );

  return (
    <form
      className="canvas-form"
      onSubmit={(e) => {
        e.preventDefault();
        onSubmit(values);
      }}
    >
      {widget.fields.map((field) => (
        <label key={field.name} className="canvas-form-row">
          <span className="meta-label">
            {field.label || field.name}
            {field.required ? ' *' : ''}
          </span>
          {field.type === 'select' ? (
            <select
              className="select"
              value={String(values[field.name] ?? '')}
              onChange={(e) => setValues((prev) => ({ ...prev, [field.name]: e.target.value }))}
              required={field.required}
            >
              <option value="">—</option>
              {field.options.map((opt) => (
                <option key={opt} value={opt}>
                  {opt}
                </option>
              ))}
            </select>
          ) : field.type === 'checkbox' ? (
            <input
              type="checkbox"
              checked={Boolean(values[field.name])}
              onChange={(e) => setValues((prev) => ({ ...prev, [field.name]: e.target.checked }))}
            />
          ) : (
            <input
              type={
                field.type === 'password'
                  ? 'password'
                  : field.type === 'email'
                    ? 'email'
                    : field.type === 'number'
                      ? 'number'
                      : 'text'
              }
              className="input"
              value={String(values[field.name] ?? '')}
              onChange={(e) => setValues((prev) => ({ ...prev, [field.name]: e.target.value }))}
              required={field.required}
            />
          )}
        </label>
      ))}
      <button type="submit" className="btn primary" disabled={busy}>
        {busy ? 'Submitting…' : widget.title || 'Submit'}
      </button>
    </form>
  );
}

function widgetTitle(widget: CanvasWidget): string {
  switch (widget.type) {
    case 'text':
      return truncate(widget.content, 72);
    case 'button':
      return widget.label;
    case 'image':
      return widget.alt || widget.url || widget.id;
    case 'list':
      return `${widget.ordered ? 'ordered' : 'unordered'} list (${widget.items.length})`;
    case 'form':
      return widget.title || `form → ${widget.submit_action}`;
    case 'table':
      return `${widget.columns.length} cols × ${widget.rows.length} rows`;
    case 'chart':
      return `${widget.chart_type} chart`;
    case 'progress':
      return widget.label || `${Math.round(widget.value * 100)}%`;
    case 'divider':
      return `${widget.orientation} divider`;
  }
}

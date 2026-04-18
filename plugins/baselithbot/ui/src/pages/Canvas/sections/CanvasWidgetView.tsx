import type { CanvasDispatchPayload, CanvasWidget } from '../../../lib/api';
import { truncate } from '../../../lib/format';
import { widgetTitle } from '../helpers';
import { FormPreview } from './FormPreview';

interface CanvasWidgetViewProps {
  widget: CanvasWidget;
  depth: number;
  onDispatch: (payload: CanvasDispatchPayload) => void;
  dispatchBusy: boolean;
}

export function CanvasWidgetView({
  widget,
  depth,
  onDispatch,
  dispatchBusy,
}: CanvasWidgetViewProps) {
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

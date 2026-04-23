import type { CanvasRenderPayload, CanvasWidget } from '../../lib/api';
import { truncate } from '../../lib/format';

export const SAMPLE_PAYLOAD: CanvasRenderPayload = {
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

export function widgetTitle(widget: CanvasWidget): string {
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

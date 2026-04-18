export interface CanvasWidgetText {
  type: 'text';
  id: string;
  content: string;
  style: Record<string, unknown>;
}

export interface CanvasWidgetButton {
  type: 'button';
  id: string;
  label: string;
  action: string;
  payload: Record<string, unknown>;
}

export interface CanvasWidgetImage {
  type: 'image';
  id: string;
  url: string | null;
  base64_png: string | null;
  alt: string;
}

export interface CanvasWidgetList {
  type: 'list';
  id: string;
  items: CanvasWidget[];
  ordered: boolean;
}

export interface CanvasFormField {
  name: string;
  label: string;
  type: 'text' | 'number' | 'password' | 'email' | 'select' | 'checkbox';
  required: boolean;
  options: string[];
  default: unknown;
}

export interface CanvasWidgetForm {
  type: 'form';
  id: string;
  title: string;
  submit_action: string;
  fields: CanvasFormField[];
}

export interface CanvasWidgetTable {
  type: 'table';
  id: string;
  columns: string[];
  rows: unknown[][];
  sortable: boolean;
}

export interface CanvasWidgetChart {
  type: 'chart';
  id: string;
  chart_type: 'line' | 'bar' | 'pie' | 'area';
  series: Record<string, unknown>[];
  x_axis: string;
  y_axis: string;
}

export interface CanvasWidgetProgress {
  type: 'progress';
  id: string;
  value: number;
  label: string;
}

export interface CanvasWidgetDivider {
  type: 'divider';
  id: string;
  orientation: 'horizontal' | 'vertical';
}

export type CanvasWidget =
  | CanvasWidgetText
  | CanvasWidgetButton
  | CanvasWidgetImage
  | CanvasWidgetList
  | CanvasWidgetForm
  | CanvasWidgetTable
  | CanvasWidgetChart
  | CanvasWidgetProgress
  | CanvasWidgetDivider;

export interface CanvasSnapshot {
  surface_id: string;
  revision: number;
  created_at: number;
  widgets: CanvasWidget[];
}

export interface CanvasRenderPayload {
  widgets: Record<string, unknown>[];
  clear?: boolean;
}

export interface CanvasDispatchPayload {
  widget_id?: string;
  action: string;
  payload?: Record<string, unknown>;
}

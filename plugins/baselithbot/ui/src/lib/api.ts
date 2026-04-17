const API_BASE = '/baselithbot';
const DASH = `${API_BASE}/dash`;

export class ApiError extends Error {
  status: number;
  body: unknown;
  constructor(status: number, message: string, body: unknown) {
    super(message);
    this.status = status;
    this.body = body;
  }
}

async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  const res = await fetch(path, {
    credentials: 'same-origin',
    headers: {
      'Content-Type': 'application/json',
      Accept: 'application/json',
      ...(init.headers ?? {}),
    },
    ...init,
  });
  const raw = await res.text();
  let body: unknown = raw;
  try {
    body = raw ? JSON.parse(raw) : null;
  } catch {
    /* keep as text */
  }
  if (!res.ok) {
    const detail =
      (body && typeof body === 'object' && 'detail' in body
        ? String((body as { detail?: unknown }).detail)
        : res.statusText) || `HTTP ${res.status}`;
    throw new ApiError(res.status, detail, body);
  }
  return body as T;
}

export interface OverviewResponse {
  agent: {
    state: string;
    backend_started: boolean;
    stealth_enabled: boolean;
  };
  counts: {
    sessions: number;
    channels_registered: number;
    channels_live: number;
    skills: number;
    cron_jobs: number;
    paired_nodes: number;
    workspaces: number;
    agents: number;
  };
  inbound: Record<string, number>;
  usage: {
    events_in_buffer: number;
    total_tokens: number;
    total_cost_usd: number;
    avg_latency_ms: number;
  };
  metrics_available: boolean;
  cron_backend: string;
}

export interface Session {
  id: string;
  title: string;
  created_at: number;
  last_active: number;
  primary: boolean;
  sandbox: Record<string, unknown> | null;
}

export interface SessionMessage {
  role: string;
  content: string;
  ts: number;
  metadata: Record<string, unknown>;
}

export interface Channel {
  name: string;
  live: boolean;
  inbound_events: number;
}

export interface Skill {
  name: string;
  version: string;
  scope: string;
  description: string;
  entrypoint: string | null;
  metadata: Record<string, unknown>;
}

export interface CronJob {
  name: string;
  interval_seconds: number;
  enabled: boolean;
  runs: number;
  next_run_at: number;
  last_error: string | null;
}

export interface PairedNode {
  node_id: string;
  platform: string;
  paired_at: number;
}

export interface UsageEvent {
  ts: number;
  session_id: string | null;
  agent_id: string | null;
  channel: string | null;
  model: string | null;
  prompt_tokens: number;
  completion_tokens: number;
  total_tokens: number;
  cost_usd: number;
  latency_ms: number;
  metadata: Record<string, unknown>;
}

export interface DoctorReport {
  platform: Record<string, string>;
  python_dependencies: Record<string, boolean>;
  system_binaries: Record<string, boolean>;
}

export interface DashboardEvent {
  type: string;
  ts: number;
  payload: Record<string, unknown>;
}

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

export type CanvasWidget =
  | CanvasWidgetText
  | CanvasWidgetButton
  | CanvasWidgetImage
  | CanvasWidgetList;

export interface CanvasSnapshot {
  surface_id: string;
  revision: number;
  created_at: number;
  widgets: CanvasWidget[];
}

export interface UsageSummaryResponse {
  events_in_buffer: number;
  total_tokens: number;
  total_cost_usd: number;
  avg_latency_ms: number;
  by_model: Record<string, { events: number; tokens: number; cost_usd: number }>;
}

export interface AgentInfo {
  name: string;
  description: string;
  keywords: string[];
  priority: number;
  metadata: Record<string, unknown>;
}

export interface WorkspaceInfo {
  name: string;
  primary: boolean;
  created_at: number;
  channels_overridden: string[];
}

export interface RunTaskRequest {
  goal: string;
  start_url?: string | null;
  max_steps?: number;
  extract_fields?: string[];
}

export interface RunTaskResult {
  success: boolean;
  final_url: string;
  steps_taken: number;
  extracted_data: Record<string, unknown>;
  history: string[];
  error: string | null;
  last_screenshot_b64: string | null;
}

export const api = {
  overview: () => request<OverviewResponse>(`${DASH}/overview`),

  sessions: () => request<{ sessions: Session[] }>(`${DASH}/sessions`),
  createSession: (title: string, primary = false) =>
    request<Session>(`${DASH}/sessions`, {
      method: 'POST',
      body: JSON.stringify({ title, primary }),
    }),
  sessionHistory: (id: string, limit = 100) =>
    request<{ session_id: string; messages: SessionMessage[] }>(
      `${DASH}/sessions/${encodeURIComponent(id)}/history?limit=${limit}`
    ),
  sendMessage: (id: string, content: string, role = 'user') =>
    request<SessionMessage>(`${DASH}/sessions/${encodeURIComponent(id)}/send`, {
      method: 'POST',
      body: JSON.stringify({ role, content, metadata: {} }),
    }),
  resetSession: (id: string) =>
    request<{ status: string }>(`${DASH}/sessions/${encodeURIComponent(id)}/reset`, {
      method: 'POST',
    }),
  deleteSession: (id: string) =>
    request<{ status: string }>(`${DASH}/sessions/${encodeURIComponent(id)}`, { method: 'DELETE' }),

  channels: () => request<{ channels: Channel[] }>(`${DASH}/channels`),
  skills: (scope?: string) =>
    request<{ skills: Skill[] }>(
      `${DASH}/skills${scope ? `?scope=${encodeURIComponent(scope)}` : ''}`
    ),

  crons: () => request<{ backend: string; jobs: CronJob[] }>(`${DASH}/crons`),
  removeCron: (name: string) =>
    request<{ status: string }>(`${DASH}/crons/${encodeURIComponent(name)}/remove`, {
      method: 'POST',
    }),

  nodes: () =>
    request<{
      paired: PairedNode[];
      status: { paired: number; pending_tokens: number; ttl_seconds: number };
    }>(`${DASH}/nodes`),
  issueToken: (platform?: string) =>
    request<{ token: string; platform: string | null }>(`${DASH}/nodes/token`, {
      method: 'POST',
      body: JSON.stringify({ platform: platform ?? null }),
    }),
  revokeNode: (id: string) =>
    request<{ status: string }>(`${DASH}/nodes/${encodeURIComponent(id)}`, { method: 'DELETE' }),

  doctor: () => request<DoctorReport>(`${DASH}/doctor`),
  canvas: () => request<CanvasSnapshot>(`${DASH}/canvas`),
  usageSummary: () => request<UsageSummaryResponse>(`${DASH}/usage/summary`),
  usageRecent: (limit = 100) =>
    request<{ events: UsageEvent[] }>(`${DASH}/usage/recent?limit=${limit}`),
  eventsRecent: (limit = 50) =>
    request<{ events: DashboardEvent[] }>(`${DASH}/events/recent?limit=${limit}`),
  prometheus: () => request<{ available: boolean; text: string }>(`${DASH}/metrics/prometheus`),
  agents: () => request<{ agents: AgentInfo[] }>(`${DASH}/agents`),
  workspaces: () => request<{ workspaces: WorkspaceInfo[] }>(`${DASH}/workspaces`),

  status: () =>
    request<{
      state: string;
      backend_started: boolean;
      stealth_enabled: boolean;
    }>(`${API_BASE}/status`),
  runTask: (payload: RunTaskRequest) =>
    request<RunTaskResult>(`${API_BASE}/run`, {
      method: 'POST',
      body: JSON.stringify(payload),
    }),
};

export const eventsStreamUrl = `${DASH}/events/stream`;

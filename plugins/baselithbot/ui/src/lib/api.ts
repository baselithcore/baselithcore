const API_BASE = '/baselithbot';
const DASH = `${API_BASE}/dash`;
const DASHBOARD_TOKEN_STORAGE_KEY = 'baselithbot.dashboard.token';

function readDashboardTokenFromQuery(): string | null {
  if (typeof window === 'undefined') return null;
  const token = new URLSearchParams(window.location.search).get('token')?.trim();
  return token || null;
}

function getDashboardToken(): string | null {
  if (typeof window === 'undefined') return null;

  const queryToken = readDashboardTokenFromQuery();
  if (queryToken) {
    try {
      window.sessionStorage.setItem(DASHBOARD_TOKEN_STORAGE_KEY, queryToken);
    } catch {
      /* ignore sessionStorage failures */
    }
    return queryToken;
  }

  try {
    const stored = window.sessionStorage.getItem(DASHBOARD_TOKEN_STORAGE_KEY)?.trim();
    return stored || null;
  } catch {
    return null;
  }
}

function withDashboardToken(path: string): string {
  const token = getDashboardToken();
  if (!token || typeof window === 'undefined') return path;

  const url = new URL(path, window.location.origin);
  if (!url.searchParams.has('token')) {
    url.searchParams.set('token', token);
  }
  return `${url.pathname}${url.search}${url.hash}`;
}

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
  const token = getDashboardToken();
  const res = await fetch(withDashboardToken(path), {
    credentials: 'same-origin',
    headers: {
      'Content-Type': 'application/json',
      Accept: 'application/json',
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
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

export type SessionReply =
  | { kind: 'none' }
  | { kind: 'slash'; result: Record<string, unknown> }
  | { kind: 'task'; run_id: string };

export interface SessionSendResponse extends SessionMessage {
  reply: SessionReply;
}

export interface Channel {
  name: string;
  live: boolean;
  configured: boolean;
  enabled: boolean;
  required_fields: string[];
  missing_fields: string[];
  inbound_events: number;
  updated_at: number | null;
}

export interface ChannelConfigDetail {
  name: string;
  required_fields: string[];
  missing_fields: string[];
  configured: boolean;
  enabled: boolean;
  live: boolean;
  safe_config: Record<string, string | number | boolean>;
  updated_at: number | null;
}

export interface ChannelTestResult {
  channel: string;
  result: Record<string, unknown>;
}

export interface Skill {
  name: string;
  version: string;
  scope: string;
  description: string;
  entrypoint: string | null;
  metadata: Record<string, unknown>;
}

export interface WorkspaceSkillValidation {
  status: 'verified' | 'provisional' | 'invalid';
  errors: string[];
  warnings: string[];
  surfaces: string[];
  tested_on: Array<Record<string, string>>;
}

export interface WorkspaceSkillReport {
  name: string;
  slug?: string;
  kind: string;
  root: string;
  entrypoint: string;
  files: Record<string, string>;
  validation: WorkspaceSkillValidation;
}

export interface CronJob {
  name: string;
  interval_seconds: number;
  enabled: boolean;
  runs: number;
  next_run_at: number;
  last_run_at: number | null;
  last_error: string | null;
  description: string;
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

export type LLMProvider = 'openai' | 'anthropic' | 'ollama' | 'huggingface';
export type VisionProvider = 'openai' | 'anthropic' | 'google' | 'ollama';

export interface FailoverEntry {
  provider: LLMProvider;
  model: string;
  cooldown_seconds: number;
}

export interface ModelPreferences {
  provider: LLMProvider;
  model: string;
  temperature: number;
  max_tokens: number | null;
  vision_provider: VisionProvider;
  vision_model: string;
  failover_chain: FailoverEntry[];
}

export interface ModelSettingsResponse {
  current: ModelPreferences;
  options: {
    llm_providers: Record<LLMProvider, string[]>;
    vision_providers: Record<VisionProvider, string[]>;
  };
}

export interface RunTaskRequest {
  run_id?: string;
  goal: string;
  start_url?: string | null;
  max_steps?: number;
  extract_fields?: string[];
}

export interface RunTaskResult {
  run_id: string | null;
  success: boolean;
  final_url: string;
  steps_taken: number;
  extracted_data: Record<string, unknown>;
  history: string[];
  error: string | null;
  last_screenshot_b64: string | null;
}

export interface ProviderKeyEntry {
  provider: string;
  configured: boolean;
  last4: string | null;
  updated_at: number | null;
}

export interface ProviderKeysResponse {
  providers: ProviderKeyEntry[];
  allowed: string[];
}

export interface ProviderKeyTestResponse {
  provider: string;
  ok: boolean;
  detail: string;
}

export interface RunTaskState {
  run_id: string;
  status: 'running' | 'completed' | 'failed';
  goal: string;
  start_url: string | null;
  max_steps: number;
  extract_fields: string[];
  started_at: number;
  completed_at: number | null;
  steps_taken: number;
  current_url: string;
  final_url: string;
  last_action: string | null;
  last_reasoning: string | null;
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
    request<SessionSendResponse>(`${DASH}/sessions/${encodeURIComponent(id)}/send`, {
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
  channelConfig: (name: string) =>
    request<ChannelConfigDetail>(`${DASH}/channels/${encodeURIComponent(name)}/config`),
  saveChannelConfig: (name: string, config: Record<string, unknown>, unsetFields: string[] = []) =>
    request<{ status: string; channel: string }>(
      `${DASH}/channels/${encodeURIComponent(name)}/config`,
      { method: 'PUT', body: JSON.stringify({ config, unset_fields: unsetFields }) }
    ),
  deleteChannelConfig: (name: string) =>
    request<{ status: string; channel: string }>(
      `${DASH}/channels/${encodeURIComponent(name)}/config`,
      { method: 'DELETE' }
    ),
  startChannel: (name: string) =>
    request<{ status: string; channel: string; adapter_status: string }>(
      `${DASH}/channels/${encodeURIComponent(name)}/start`,
      { method: 'POST' }
    ),
  stopChannel: (name: string) =>
    request<{ status: string; channel: string }>(
      `${DASH}/channels/${encodeURIComponent(name)}/stop`,
      { method: 'POST' }
    ),
  testChannel: (name: string, target: string, text: string) =>
    request<ChannelTestResult>(`${DASH}/channels/${encodeURIComponent(name)}/test`, {
      method: 'POST',
      body: JSON.stringify({ target, text }),
    }),
  skills: (scope?: string) =>
    request<{ skills: Skill[] }>(
      `${DASH}/skills${scope ? `?scope=${encodeURIComponent(scope)}` : ''}`
    ),
  workspaceSkillValidation: () =>
    request<{
      reports: WorkspaceSkillReport[];
      counts: { verified: number; provisional: number; invalid: number };
    }>(`${DASH}/skills/workspace/validate`),
  clawhubStatus: () =>
    request<{
      base_url: string;
      install_dir: string;
      timeout_seconds: number;
      auth_token_set: boolean;
    }>(`${DASH}/skills/clawhub`),
  configureClawhub: (payload: {
    base_url?: string | null;
    auth_token?: string | null;
    install_dir?: string | null;
    timeout_seconds?: number | null;
  }) =>
    request<{
      base_url: string;
      install_dir: string;
      timeout_seconds: number;
      auth_token_set: boolean;
    }>(`${DASH}/skills/clawhub`, {
      method: 'PUT',
      body: JSON.stringify(payload),
    }),
  clawhubCatalog: () =>
    request<{ entries: Array<Record<string, unknown>> }>(`${DASH}/skills/clawhub/catalog`),
  clawhubSync: () =>
    request<{ status: string; installed: number; errors: unknown[] }>(
      `${DASH}/skills/clawhub/sync`,
      { method: 'POST' }
    ),
  clawhubInstall: (name: string) =>
    request<{ status: string; name: string; bytes: number; path: string }>(
      `${DASH}/skills/clawhub/install/${encodeURIComponent(name)}`,
      { method: 'POST' }
    ),
  rescanSkills: () =>
    request<{ removed: number; workspace_skills: Skill[] }>(`${DASH}/skills/rescan`, {
      method: 'POST',
    }),
  removeSkill: (name: string) =>
    request<{ status: string; name: string; scope: string }>(
      `${DASH}/skills/${encodeURIComponent(name)}`,
      { method: 'DELETE' }
    ),

  crons: () => request<{ backend: string; jobs: CronJob[] }>(`${DASH}/crons`),
  removeCron: (name: string) =>
    request<{ status: string }>(`${DASH}/crons/${encodeURIComponent(name)}/remove`, {
      method: 'POST',
    }),
  toggleCron: (name: string, enabled: boolean) =>
    request<{ status: string; name: string; job: CronJob | null }>(
      `${DASH}/crons/${encodeURIComponent(name)}/toggle`,
      { method: 'POST', body: JSON.stringify({ enabled }) }
    ),
  runCron: (name: string) =>
    request<{ status: string; name: string }>(`${DASH}/crons/${encodeURIComponent(name)}/run`, {
      method: 'POST',
    }),
  updateCronInterval: (name: string, intervalSeconds: number) =>
    request<{ status: string; name: string; job: CronJob | null }>(
      `${DASH}/crons/${encodeURIComponent(name)}`,
      {
        method: 'PATCH',
        body: JSON.stringify({ interval_seconds: intervalSeconds }),
      }
    ),

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

  models: () => request<ModelSettingsResponse>(`${DASH}/models`),
  updateModels: (prefs: ModelPreferences) =>
    request<{ current: ModelPreferences }>(`${DASH}/models`, {
      method: 'PUT',
      body: JSON.stringify(prefs),
    }),

  providerKeys: () => request<ProviderKeysResponse>(`${DASH}/provider-keys`),
  setProviderKey: (provider: string, apiKey: string) =>
    request<ProviderKeyEntry>(`${DASH}/provider-keys/${encodeURIComponent(provider)}`, {
      method: 'PUT',
      body: JSON.stringify({ api_key: apiKey }),
    }),
  deleteProviderKey: (provider: string) =>
    request<{ provider: string; removed: boolean }>(
      `${DASH}/provider-keys/${encodeURIComponent(provider)}`,
      { method: 'DELETE' }
    ),
  testProviderKey: (provider: string) =>
    request<ProviderKeyTestResponse>(`${DASH}/provider-keys/${encodeURIComponent(provider)}/test`, {
      method: 'POST',
    }),

  doctor: () => request<DoctorReport>(`${DASH}/doctor`),
  canvas: () => request<CanvasSnapshot>(`${DASH}/canvas`),
  usageSummary: () => request<UsageSummaryResponse>(`${DASH}/usage/summary`),
  usageRecent: (limit = 100) =>
    request<{ events: UsageEvent[] }>(`${DASH}/usage/recent?limit=${limit}`),
  runTaskLatest: () => request<{ run: RunTaskState | null }>(`${DASH}/run-task/latest`),
  runTaskRecent: (limit = 8) =>
    request<{ runs: RunTaskState[] }>(`${DASH}/run-task/recent?limit=${limit}`),
  runTaskById: (runId: string) =>
    request<{ run: RunTaskState }>(`${DASH}/run-task/${encodeURIComponent(runId)}`),
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

export const eventsStreamUrl = withDashboardToken(`${DASH}/events/stream`);

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
    canvas_widgets: number;
    provider_keys_total: number;
    provider_keys_configured: number;
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
  custom?: boolean;
}

export interface CronActionCatalogEntry {
  type: string;
  label: string;
  description: string;
  params_schema: Record<string, unknown>;
}

export interface CronCatalog {
  actions: CronActionCatalogEntry[];
  name_prefix: string;
}

export interface CustomCronPayload {
  name: string;
  interval_seconds: number;
  action: { type: string; params: Record<string, unknown> };
  description?: string;
  enabled?: boolean;
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

export interface DoctorPathInfo {
  path: string;
  exists: boolean;
  kind: 'dir' | 'file' | 'missing' | 'other';
  size_bytes: number | null;
  writable?: boolean;
}

export interface DoctorPluginRuntime {
  agent: { state: string; backend_started: boolean; stealth_enabled: boolean };
  cron: { backend: string; running: boolean; jobs: number; custom_jobs: number };
  channels: { known: number; live: number };
  sessions: { count: number };
  skills: { count: number };
  workspaces: { count: number };
  agents: { total: number; system: number; custom: number };
  provider_keys: { total: number; configured: number };
  nodes: { paired: number };
  canvas: { widgets: number };
  usage: Record<string, number>;
  inbound: Record<string, number>;
}

export interface DoctorReport {
  platform: Record<string, string>;
  python_dependencies: Record<string, boolean>;
  system_binaries: Record<string, boolean>;
  plugin_runtime?: DoctorPluginRuntime;
  state_paths?: Record<string, DoctorPathInfo>;
}

export interface DashboardEvent {
  type: string;
  ts: number;
  payload: Record<string, unknown>;
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
  custom: boolean;
  kind: string;
}

export interface AgentsListResponse {
  agents: AgentInfo[];
  name_prefix: string;
  totals: { all: number; custom: number; system: number };
}

export interface AgentActionCatalogEntry {
  type: string;
  label: string;
  description: string;
  params_schema: Record<string, unknown>;
}

export interface AgentActionPayload {
  type: string;
  params: Record<string, unknown>;
}

export interface CustomAgentPayload {
  name: string;
  description: string;
  keywords: string[];
  priority: number;
  metadata: Record<string, unknown>;
  action: AgentActionPayload;
}

export interface CustomAgentUpdatePayload {
  description: string;
  keywords: string[];
  priority: number;
  metadata: Record<string, unknown>;
  action: AgentActionPayload;
}

export interface AgentDispatchResult {
  status: string;
  name: string;
  result: Record<string, unknown>;
}

export interface WorkspaceInfo {
  name: string;
  description: string;
  primary: boolean;
  created_at: number;
  channels_overridden: string[];
  metadata: Record<string, unknown>;
}

export interface WorkspaceCreatePayload {
  name: string;
  description?: string;
  primary?: boolean;
  channel_overrides?: Record<string, Record<string, unknown>>;
  metadata?: Record<string, unknown>;
}

export interface WorkspaceUpdatePayload {
  description?: string;
  primary?: boolean;
  channel_overrides?: Record<string, Record<string, unknown>>;
  metadata?: Record<string, unknown>;
}

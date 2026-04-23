import { API_BASE, DASH, request, withDashboardToken } from './client';
import type {
  AgentActionCatalogEntry,
  AgentDispatchResult,
  AgentInfo,
  AgentsListResponse,
  CanvasDispatchPayload,
  CanvasRenderPayload,
  CanvasSnapshot,
  Channel,
  ChannelConfigDetail,
  ChannelTestResult,
  CronCatalog,
  CronJob,
  CustomAgentPayload,
  CustomAgentUpdatePayload,
  CustomCronPayload,
  DashboardEvent,
  DoctorReport,
  OverviewResponse,
  PairedNode,
  Session,
  SessionMessage,
  SessionSendResponse,
  Skill,
  UsageEvent,
  UsageSummaryResponse,
  WorkspaceCreatePayload,
  WorkspaceInfo,
  WorkspaceSkillCreatePayload,
  WorkspaceSkillReport,
  WorkspaceSkillSpec,
  WorkspaceUpdatePayload,
} from './types';
import type {
  ApprovalListResponse,
  AuditLogResponse,
  ComputerUseConfig,
  DesktopTaskDispatchRequest,
  DesktopTaskDispatchResponse,
  DesktopToolCatalog,
  DesktopToolInvocation,
  ModelPreferences,
  ModelSettingsResponse,
  ProviderKeyEntry,
  ProviderKeyTestResponse,
  ProviderKeysResponse,
  ReplayRun,
  ReplayRunsResponse,
  RunTaskRequest,
  RunTaskResult,
  RunTaskState,
  StealthConfig,
} from './types';

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
  createWorkspaceSkill: (payload: WorkspaceSkillCreatePayload) =>
    request<{ status: string; spec: WorkspaceSkillSpec }>(`${DASH}/skills/workspace`, {
      method: 'POST',
      body: JSON.stringify(payload),
    }),
  removeSkill: (name: string) =>
    request<{ status: string; name: string; scope: string; purged_files: boolean }>(
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
  cronCatalog: () => request<CronCatalog>(`${DASH}/crons/catalog`),
  createCustomCron: (payload: CustomCronPayload) =>
    request<{ status: string; job: CustomCronPayload & { name: string } }>(`${DASH}/crons`, {
      method: 'POST',
      body: JSON.stringify(payload),
    }),
  updateCustomCron: (name: string, payload: Omit<CustomCronPayload, 'name'>) =>
    request<{ status: string; job: CustomCronPayload }>(
      `${DASH}/crons/${encodeURIComponent(name)}/custom`,
      { method: 'PUT', body: JSON.stringify(payload) }
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
  canvasRender: (payload: CanvasRenderPayload) =>
    request<{ status: string; snapshot: CanvasSnapshot }>(`${DASH}/canvas/render`, {
      method: 'POST',
      body: JSON.stringify(payload),
    }),
  canvasClear: () =>
    request<{ status: string; snapshot: CanvasSnapshot }>(`${DASH}/canvas/clear`, {
      method: 'POST',
    }),
  canvasDispatch: (payload: CanvasDispatchPayload) =>
    request<{
      status: string;
      widget_id: string;
      action: string;
      payload: Record<string, unknown>;
    }>(`${DASH}/canvas/dispatch`, {
      method: 'POST',
      body: JSON.stringify(payload),
    }),
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
  agents: () => request<AgentsListResponse>(`${DASH}/agents`),
  agentsCatalog: () =>
    request<{ actions: AgentActionCatalogEntry[]; name_prefix: string }>(`${DASH}/agents/catalog`),
  createCustomAgent: (payload: CustomAgentPayload) =>
    request<{ status: string; agent: AgentInfo }>(`${DASH}/agents`, {
      method: 'POST',
      body: JSON.stringify(payload),
    }),
  updateCustomAgent: (name: string, payload: CustomAgentUpdatePayload) =>
    request<{ status: string; agent: AgentInfo }>(`${DASH}/agents/${encodeURIComponent(name)}`, {
      method: 'PUT',
      body: JSON.stringify(payload),
    }),
  deleteCustomAgent: (name: string) =>
    request<{ status: string; name: string }>(`${DASH}/agents/${encodeURIComponent(name)}`, {
      method: 'DELETE',
    }),
  dispatchAgent: (name: string, query: string, context: Record<string, unknown> = {}) =>
    request<AgentDispatchResult>(`${DASH}/agents/${encodeURIComponent(name)}/dispatch`, {
      method: 'POST',
      body: JSON.stringify({ query, context }),
    }),
  workspaces: () => request<{ workspaces: WorkspaceInfo[] }>(`${DASH}/workspaces`),
  createWorkspace: (payload: WorkspaceCreatePayload) =>
    request<{ status: string; workspace: WorkspaceInfo }>(`${DASH}/workspaces`, {
      method: 'POST',
      body: JSON.stringify(payload),
    }),
  updateWorkspace: (name: string, payload: WorkspaceUpdatePayload) =>
    request<{ status: string; workspace: WorkspaceInfo }>(
      `${DASH}/workspaces/${encodeURIComponent(name)}`,
      {
        method: 'PUT',
        body: JSON.stringify(payload),
      }
    ),
  deleteWorkspace: (name: string) =>
    request<{ status: string; name: string }>(`${DASH}/workspaces/${encodeURIComponent(name)}`, {
      method: 'DELETE',
    }),

  computerUse: () => request<{ current: ComputerUseConfig }>(`${DASH}/computer-use`),
  updateComputerUse: (config: ComputerUseConfig) =>
    request<{ current: ComputerUseConfig }>(`${DASH}/computer-use`, {
      method: 'PUT',
      body: JSON.stringify(config),
    }),
  desktopTools: () => request<DesktopToolCatalog>(`${DASH}/desktop/tools`),
  invokeDesktopTool: (toolName: string, args: Record<string, unknown>) =>
    request<DesktopToolInvocation>(`${DASH}/desktop/tools/${encodeURIComponent(toolName)}`, {
      method: 'POST',
      body: JSON.stringify({ args }),
    }),
  desktopTaskDispatch: (payload: DesktopTaskDispatchRequest) =>
    request<DesktopTaskDispatchResponse>(`${DASH}/desktop/task`, {
      method: 'POST',
      body: JSON.stringify(payload),
    }),
  desktopTaskLatest: () => request<{ run: RunTaskState | null }>(`${DASH}/desktop/task/latest`),
  desktopTaskRecent: (limit = 8) =>
    request<{ runs: RunTaskState[] }>(`${DASH}/desktop/task/recent?limit=${limit}`),
  desktopTaskById: (runId: string) =>
    request<{ run: RunTaskState }>(`${DASH}/desktop/task/${encodeURIComponent(runId)}`),
  desktopTaskCancel: (runId: string) =>
    request<{ run_id: string; cancel_requested: boolean }>(
      `${DASH}/desktop/task/${encodeURIComponent(runId)}/cancel`,
      { method: 'POST' }
    ),
  stealth: () => request<{ current: StealthConfig }>(`${DASH}/stealth`),
  updateStealth: (config: StealthConfig) =>
    request<{ current: StealthConfig }>(`${DASH}/stealth`, {
      method: 'PUT',
      body: JSON.stringify(config),
    }),
  auditLog: (limit = 200, action?: string) => {
    const params = new URLSearchParams({ limit: String(limit) });
    if (action) params.set('action', action);
    return request<AuditLogResponse>(`${DASH}/audit-log?${params.toString()}`);
  },
  approvals: () => request<ApprovalListResponse>(`${DASH}/approvals`),
  approveRequest: (id: string, reason?: string) =>
    request<{ status: string; id: string }>(`${DASH}/approvals/${encodeURIComponent(id)}/approve`, {
      method: 'POST',
      body: JSON.stringify({ reason: reason ?? null }),
    }),
  denyRequest: (id: string, reason?: string) =>
    request<{ status: string; id: string }>(`${DASH}/approvals/${encodeURIComponent(id)}/deny`, {
      method: 'POST',
      body: JSON.stringify({ reason: reason ?? null }),
    }),
  replayRuns: (limit = 50) => request<ReplayRunsResponse>(`${DASH}/replay/runs?limit=${limit}`),
  replayRun: (runId: string) =>
    request<{ run: ReplayRun }>(`${DASH}/replay/runs/${encodeURIComponent(runId)}`),

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

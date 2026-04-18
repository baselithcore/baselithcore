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

export interface ComputerUseConfig {
  enabled: boolean;
  allow_mouse: boolean;
  allow_keyboard: boolean;
  allow_screenshot: boolean;
  allow_shell: boolean;
  allow_filesystem: boolean;
  allowed_shell_commands: string[];
  shell_timeout_seconds: number;
  filesystem_root: string | null;
  filesystem_max_bytes: number;
  audit_log_path: string | null;
  require_approval_for: string[];
  approval_timeout_seconds: number;
}

export interface DesktopToolSpec {
  name: string;
  description: string;
  input_schema: {
    type: string;
    properties?: Record<
      string,
      {
        type?: string;
        enum?: unknown[];
        default?: unknown;
        items?: { type?: string };
        minimum?: number;
        maximum?: number;
      }
    >;
    required?: string[];
  };
}

export interface DesktopToolPolicy {
  enabled: boolean;
  allow_mouse: boolean;
  allow_keyboard: boolean;
  allow_screenshot: boolean;
  allow_shell: boolean;
  allow_filesystem: boolean;
  allowed_shell_commands: string[];
  filesystem_root: string | null;
  filesystem_max_bytes: number;
  shell_timeout_seconds: number;
  audit_log_path: string | null;
  require_approval_for: string[];
  approval_timeout_seconds: number;
}

export interface DesktopToolCatalog {
  policy: DesktopToolPolicy;
  tools: DesktopToolSpec[];
}

export interface DesktopToolInvocation {
  tool: string;
  result: {
    status: 'success' | 'denied' | 'error' | string;
    error?: string;
    [key: string]: unknown;
  };
}

export interface DesktopTaskDispatchRequest {
  goal: string;
  max_steps?: number;
  run_id?: string;
}

export interface DesktopTaskDispatchResponse {
  run_id: string;
  status: 'running' | 'completed' | 'failed';
  started_at: number;
}

export type ApprovalStatus = 'pending' | 'approved' | 'denied' | 'timed_out';

export interface ApprovalRequest {
  id: string;
  capability: string;
  action: string;
  params: Record<string, unknown>;
  submitted_at: number;
  timeout_seconds: number;
  status: ApprovalStatus;
  resolved_at: number | null;
  reason: string | null;
  expires_at: number;
}

export interface ApprovalPolicy {
  enabled: boolean;
  approval_timeout_seconds: number;
  enabled_capabilities: string[];
  gated_capabilities: string[];
  bypassed_capabilities: string[];
}

export interface ApprovalListResponse {
  pending: ApprovalRequest[];
  history: ApprovalRequest[];
  totals: { pending: number; history: number; approved: number; denied: number; timed_out: number };
  status_counts: Partial<Record<ApprovalStatus, number>>;
  capability_counts: Record<string, number>;
  action_counts: Record<string, number>;
  oldest_pending_ts: number | null;
  next_expiry_ts: number | null;
  latest_resolved_ts: number | null;
  policy: ApprovalPolicy;
}

export interface ReplayRunSummary {
  run_id: string;
  goal: string;
  start_url: string | null;
  max_steps: number;
  status: string;
  started_at: number;
  completed_at: number | null;
  final_url: string | null;
  error: string | null;
  step_count: number;
}

export interface ReplayStep {
  step_index: number;
  ts: number;
  action: string;
  reasoning: string;
  current_url: string;
  screenshot_b64: string | null;
  extracted_data: Record<string, unknown>;
}

export interface ReplayRun extends ReplayRunSummary {
  extracted_data: Record<string, unknown>;
  screenshot_steps: number;
  first_step_ts: number | null;
  last_step_ts: number | null;
  distinct_url_count: number;
  steps: ReplayStep[];
}

export interface ReplayRunsResponse {
  runs: ReplayRunSummary[];
  returned: number;
  status_counts: Record<string, number>;
  step_totals: number;
  active_runs: number;
  latest_started_ts: number | null;
  latest_completed_ts: number | null;
  path: string;
  retention_days: number;
}

export interface StealthConfig {
  enabled: boolean;
  rotate_user_agent: boolean;
  mask_webdriver: boolean;
  spoof_languages: string[];
  spoof_timezone: string;
  user_agents: string[];
}

export interface AuditEntry {
  ts?: number;
  action?: string;
  status?: string;
  raw?: string;
  [key: string]: unknown;
}

export interface AuditLogResponse {
  configured: boolean;
  path: string | null;
  file_exists?: boolean;
  entries: AuditEntry[];
  returned?: number;
  tail_window?: number;
  scanned_rows?: number;
  status_counts?: Record<string, number>;
  action_counts?: Record<string, number>;
  oldest_ts?: number | null;
  newest_ts?: number | null;
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

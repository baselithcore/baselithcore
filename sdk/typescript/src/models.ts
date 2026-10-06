/** Request/response types mirroring the server's public contract. */

export interface ChatRequest {
  query: string;
  conversation_id?: string;
  rag_only?: boolean;
  kb_label?: string;
  tenant_id?: string;
  max_response_tokens?: number;
}

export interface ChatResponse {
  answer: string;
  metadata?: Record<string, unknown>;
  sources?: Array<Record<string, unknown>>;
  conversation_id?: string;
}

export interface FeedbackRequest {
  query: string;
  answer: string;
  feedback: 'positive' | 'negative';
  conversation_id?: string;
  sources?: Array<Record<string, unknown>>;
  comment?: string;
}

export interface HealthStatus {
  status: string;
  [key: string]: unknown;
}

export interface ReadinessStatus {
  status: string;
  services?: Record<string, boolean>;
  cached?: boolean;
  [key: string]: unknown;
}

// --- Async agent runs (`/agent/async`, `/agent/status/{task_id}`) ---

/** Submission payload for an async agent run. */
export interface AgentRunRequest {
  query: string;
  conversation_id?: string;
}

/** `202 Accepted` for a queued run; `location` is the `Location` header. */
export interface AgentRunSubmission {
  task_id: string;
  status_url: string;
  location?: string;
  [key: string]: unknown;
}

/** Task states reported by `GET /agent/status/{task_id}`. */
export type AgentRunState = 'pending' | 'queued' | 'running' | 'completed' | 'failed' | 'cancelled';

/** The task tracker record for a queued run. */
export interface AgentRunStatus {
  status: AgentRunState | (string & {});
  progress?: number;
  message?: string;
  updated_at?: string;
  result?: unknown;
  error?: string;
  [key: string]: unknown;
}

// --- Runs (`/runs/{run_id}/...`) ---

/** One structured agent event from `GET /runs/{run_id}/events`. */
export interface RunEvent {
  /** `run_started`, `thought`, `tool_call`, `tool_result`, `memory`, `human`, `chunk`, `final`, `error`. */
  type: string;
  /** The SSE `id:` (the `AgentEvent` id). */
  id?: string;
  content?: string | null;
  data: Record<string, unknown>;
  agent_id?: string;
  timestamp?: string;
  [key: string]: unknown;
}

/** Common shape of every cursor-paginated list response. */
export interface Page {
  count: number;
  next_cursor: string | null;
  has_more: boolean;
}

/** Version-ascending snapshot summaries (`GET /runs/{run_id}/history`). */
export interface RunHistoryPage extends Page {
  run_id: string;
  history: Array<Record<string, unknown>>;
}

// --- Approvals (`/approvals`) ---

/** A run durably paused awaiting a reviewer decision. */
export interface PendingApproval {
  run_id: string;
  tenant_id?: string | null;
  query?: string | null;
  intent?: string | null;
  pending_approval: Record<string, unknown>;
  updated_at?: number | null;
}

/** A page of pending approvals (`GET /approvals`), newest first. */
export interface ApprovalPage extends Page {
  pending: PendingApproval[];
}

/** Reviewer decision; `approver` is a display label only (the server records the authenticated principal). */
export interface ApprovalDecisionRequest {
  approved: boolean;
  approver?: string;
  reason?: string;
}

export interface ApprovalDecisionResult {
  run_id: string;
  recorded: boolean;
  approved: boolean;
}

export interface RunResumeResult {
  run_id: string;
  result: unknown;
}

// --- Webhooks (`/webhooks`) ---

/** Subscription payload; `event_types` defaults to `["*"]` (all events). */
export interface WebhookCreateRequest {
  url: string;
  event_types?: string[];
  description?: string;
  headers?: Record<string, string>;
}

/** A webhook subscription as the API returns it (signing secret redacted). */
export interface WebhookEndpoint {
  id: string;
  url: string;
  tenant_id?: string;
  event_types: string[];
  enabled: boolean;
  description?: string | null;
  headers?: Record<string, string>;
  created_at?: number;
  has_secret?: boolean;
  [key: string]: unknown;
}

/** A registered endpoint plus its signing `secret` — returned only once. */
export interface WebhookCreated {
  endpoint: WebhookEndpoint;
  secret: string;
}

export interface WebhookPage extends Page {
  endpoints: WebhookEndpoint[];
}

/** The record of attempting to deliver one event to one endpoint. */
export interface WebhookDelivery {
  id: string;
  endpoint_id: string;
  event_id: string;
  event_type: string;
  url?: string;
  tenant_id?: string;
  status: 'pending' | 'success' | 'failed' | (string & {});
  attempts: number;
  last_status_code?: number | null;
  last_error?: string | null;
  created_at?: number;
  completed_at?: number | null;
  payload?: Record<string, unknown>;
  [key: string]: unknown;
}

export interface WebhookDeliveryPage extends Page {
  deliveries: WebhookDelivery[];
}

export interface WebhookReplay {
  status: string;
  delivery: WebhookDelivery;
}

export interface WebhookDeleted {
  status: string;
  endpoint_id: string;
}

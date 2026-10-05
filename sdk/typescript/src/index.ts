/** BaselithCore TypeScript SDK — a typed client for the BaselithCore API. */

export { BaselithClient, ChatStreamError } from './client.js';
export type { BaselithClientOptions } from './client.js';
export { iterPages, TERMINAL_RUN_STATUSES } from './pagination.js';
export type { PageOptions } from './pagination.js';
export { TERMINAL_EVENT_TYPES } from './sse.js';
export type {
  AgentRunRequest,
  AgentRunState,
  AgentRunStatus,
  AgentRunSubmission,
  ApprovalDecisionRequest,
  ApprovalDecisionResult,
  ApprovalPage,
  ChatRequest,
  ChatResponse,
  FeedbackRequest,
  HealthStatus,
  Page,
  PendingApproval,
  ReadinessStatus,
  RunEvent,
  RunHistoryPage,
  RunResumeResult,
  WebhookCreated,
  WebhookCreateRequest,
  WebhookDeleted,
  WebhookDelivery,
  WebhookDeliveryPage,
  WebhookEndpoint,
  WebhookPage,
  WebhookReplay,
} from './models.js';
export {
  ApiConnectionError,
  AuthenticationError,
  BaselithApiError,
  BaselithConfigError,
  BaselithError,
  NotFoundError,
  PermissionDeniedError,
  RateLimitError,
  RunTimeoutError,
  ServerError,
  errorFromResponse,
} from './errors.js';

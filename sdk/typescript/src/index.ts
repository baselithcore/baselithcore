/** BaselithCore TypeScript SDK — a typed client for the BaselithCore API. */

export { BaselithClient } from './client.js';
export type { BaselithClientOptions } from './client.js';
export type {
  ChatRequest,
  ChatResponse,
  FeedbackRequest,
  HealthStatus,
  ReadinessStatus,
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
  ServerError,
  errorFromResponse,
} from './errors.js';

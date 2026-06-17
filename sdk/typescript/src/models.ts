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

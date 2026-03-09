"""
Core Observability Metrics Module.

Provides Prometheus metrics for monitoring the baselith-core.
All metrics use the 'mas_' prefix (Baselith-Core).
"""

from __future__ import annotations

from prometheus_client import Counter, Gauge, Histogram

# === Chat Metrics ===
CHAT_REQUESTS_TOTAL = Counter(
    "mas_chat_requests_total",
    "Total number of chat requests received.",
    ["route"],
)
CHAT_REQUEST_LATENCY_SECONDS = Histogram(
    "mas_chat_request_latency_seconds",
    "Distribution of chat request latency.",
    ["route"],
)
CHAT_REQUEST_ERRORS_TOTAL = Counter(
    "mas_chat_request_errors_total",
    "Total number of failed chat requests.",
    ["route", "reason"],
)

# === Rerank Metrics ===
RERANK_REQUESTS_TOTAL = Counter(
    "mas_rerank_requests_total",
    "Total number of rerank requests.",
)
RERANK_LATENCY_SECONDS = Histogram(
    "mas_rerank_latency_seconds",
    "Distribution of reranker (CrossEncoder) latency.",
)
RERANK_CACHE_HIT_TOTAL = Counter(
    "mas_rerank_cache_hit_total",
    "Total rerank cache hits.",
)
RERANK_CACHE_MISS_TOTAL = Counter(
    "mas_rerank_cache_miss_total",
    "Total rerank cache misses.",
)

# === Indexing Metrics ===
INDEXING_RUNS_TOTAL = Counter(
    "mas_indexing_runs_total",
    "Total number of document indexing runs.",
    ["mode"],
)
INDEXING_DURATION_SECONDS = Histogram(
    "mas_indexing_duration_seconds",
    "Distribution of indexing duration.",
    ["mode"],
)
RETRIEVAL_LATENCY_SECONDS = Histogram(
    "mas_retrieval_latency_seconds",
    "Distribution of retrieval latency.",
)
INDEXED_DOCUMENTS_TOTAL = Counter(
    "mas_indexed_documents_total",
    "Cumulative count of documents indexed.",
)
INDEXED_DOCUMENTS_GAUGE = Gauge(
    "mas_indexed_documents_current",
    "Current number of indexed documents available.",
)

# === LLM Metrics ===
LLM_REQUESTS_TOTAL = Counter(
    "mas_llm_requests_total",
    "Total number of LLM requests.",
    ["model", "operation"],
)
LLM_TOKENS_TOTAL = Counter(
    "mas_llm_tokens_total",
    "Total tokens consumed by LLM calls.",
    ["model", "token_type"],  # token_type: input/output
)
LLM_LATENCY_SECONDS = Histogram(
    "mas_llm_latency_seconds",
    "Distribution of LLM request latency.",
    ["model"],
)
LLM_ERRORS_TOTAL = Counter(
    "mas_llm_errors_total",
    "Total number of LLM errors.",
    ["model", "error_type"],
)

# === Plugin Metrics ===
PLUGIN_LOAD_TOTAL = Counter(
    "mas_plugin_load_total",
    "Total number of plugins loaded.",
    ["plugin_name", "status"],
)
PLUGIN_CALL_LATENCY_SECONDS = Histogram(
    "mas_plugin_call_latency_seconds",
    "Distribution of plugin call latency.",
    ["plugin_name", "operation"],
)

# === Agent Metrics ===
AGENT_STEPS_TOTAL = Counter(
    "mas_agent_steps_total",
    "Total number of agent execution steps.",
    ["agent_type", "step_type"],
)
AGENT_TOOL_CALLS_TOTAL = Counter(
    "mas_agent_tool_calls_total",
    "Total number of tool calls by agents.",
    ["agent_type", "tool_name"],
)

# === Authentication Metrics ===
AUTH_REQUESTS_TOTAL = Counter(
    "mas_auth_requests_total",
    "Total number of authentication requests.",
    ["method", "status"],
)
AUTH_TOKEN_VALIDATION_SECONDS = Histogram(
    "mas_auth_token_validation_seconds",
    "Distribution of token validation latency.",
    ["method"],
)

# === Feedback Metrics ===
FEEDBACK_RECEIVED_TOTAL = Counter(
    "mas_feedback_total",
    "Total feedback received by sentiment.",
    ["sentiment"],
)


__all__ = [
    # Chat
    "CHAT_REQUESTS_TOTAL",
    "CHAT_REQUEST_LATENCY_SECONDS",
    "CHAT_REQUEST_ERRORS_TOTAL",
    # Rerank
    "RERANK_REQUESTS_TOTAL",
    "RERANK_LATENCY_SECONDS",
    "RERANK_CACHE_HIT_TOTAL",
    "RERANK_CACHE_MISS_TOTAL",
    # Indexing
    "INDEXING_RUNS_TOTAL",
    "INDEXING_DURATION_SECONDS",
    "RETRIEVAL_LATENCY_SECONDS",
    "INDEXED_DOCUMENTS_TOTAL",
    "INDEXED_DOCUMENTS_GAUGE",
    # LLM
    "LLM_REQUESTS_TOTAL",
    "LLM_TOKENS_TOTAL",
    "LLM_LATENCY_SECONDS",
    "LLM_ERRORS_TOTAL",
    # Plugin
    "PLUGIN_LOAD_TOTAL",
    "PLUGIN_CALL_LATENCY_SECONDS",
    # Agent
    "AGENT_STEPS_TOTAL",
    "AGENT_TOOL_CALLS_TOTAL",
    # Auth
    "AUTH_REQUESTS_TOTAL",
    "AUTH_TOKEN_VALIDATION_SECONDS",
    # Feedback
    "FEEDBACK_RECEIVED_TOTAL",
]

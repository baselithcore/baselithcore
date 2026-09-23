"""
General Application Configuration for BaselithCore.

This module defines high-level settings that govern the runtime behavior of
the application, including server parameters, multi-tenancy rules,
observability (logging/telemetry), cost controls, and safety guardrails.
"""

import logging
from typing import Annotated, Any
from zoneinfo import ZoneInfo

from pydantic import AliasChoices, Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

from core.config._collections import csv_list

logger = logging.getLogger(__name__)


def _resolve_service_version() -> str:
    """Best-effort lookup of the installed package version for telemetry."""
    try:
        from core._version import __version__

        return f"{__version__}"
    except Exception:
        return "0.0.0"


class AppConfig(BaseSettings):
    """
    Main application configuration schema.

    Settings are loaded from environment variables (case-insensitive)
    or a `.env` file.
    """

    model_config = SettingsConfigDict(
        case_sensitive=False,
        extra="ignore",
    )

    # === Server & Network ===
    # Network interface to bind the application server to.
    host: str = Field(default="0.0.0.0", alias="HOST")  # nosec B104  # noqa: S104
    # Port to listen on.
    port: int = Field(default=8000, alias="PORT")
    # Site-relative path ``GET /`` redirects to. The framework serves nothing
    # at the root: a deployment's homepage is one of the plugin SPAs it
    # installed (``/<plugin>/``), which core cannot guess, so ``/`` answers 404
    # until this names the landing. Empty (the default) keeps that 404 — no
    # deployment gains a redirect it did not ask for.
    root_redirect: str = Field(default="", alias="BASELITH_ROOT_REDIRECT")

    # === Multi-Tenancy ===
    # If True, enforces strict logical isolation between different tenants.
    strict_tenant_isolation: bool = Field(default=True, alias="STRICT_TENANT_ISOLATION")

    # === Logging ===
    log_level_console: str = Field(default="INFO", alias="LOG_LEVEL_CONSOLE")
    log_level_file: str = Field(default="INFO", alias="LOG_LEVEL_FILE")
    # Enable structured JSON logging (recommended for production/k8s).
    log_json: bool = Field(default=True, alias="LOG_JSON")
    # Mask sensitive data (PII, tokens) in logs.
    log_masking_enabled: bool = Field(default=True, alias="LOG_MASKING_ENABLED")

    # === Bootstrap ===
    # Perform indexing and initialization as a background task.
    index_bootstrap_background: bool = Field(
        default=True, alias="INDEX_BOOTSTRAP_BACKGROUND"
    )
    index_bootstrap_enabled: bool = Field(default=True, alias="INDEX_BOOTSTRAP_ENABLED")

    # === Observability & Telemetry ===
    telemetry_enabled: bool = Field(default=False, alias="TELEMETRY_ENABLED")
    # OpenTelemetry collector endpoint for traces, metrics and logs. The default
    # is the OTLP/gRPC port; switch to :4318 when selecting `http/protobuf`
    # below (the per-signal `/v1/...` path is appended for you).
    telemetry_otel_endpoint: str = Field(
        default="http://localhost:4317", alias="TELEMETRY_OTEL_ENDPOINT"
    )
    # OTLP wire protocol: `grpc` (default) or `http/protobuf`. HTTP is what a
    # collector's `otlphttp` receiver speaks, what most vendor ingest endpoints
    # expose, and the only option behind an L7 proxy that will not forward
    # HTTP/2 trailers. `OTEL_EXPORTER_OTLP_PROTOCOL` is the specification's own
    # name for this knob, so it is accepted as an alias — a sidecar or chart
    # that already sets it is honoured without a Baselith-specific variable.
    telemetry_otel_protocol: str = Field(
        default="grpc",
        validation_alias=AliasChoices(
            "TELEMETRY_OTEL_PROTOCOL", "OTEL_EXPORTER_OTLP_PROTOCOL"
        ),
    )
    # Head-based trace sampling ratio (ParentBased(TraceIdRatio)). 1.0 = all
    # traces, 0.0 = none. Lower in high-traffic production to cap cost.
    telemetry_traces_sample_rate: float = Field(
        default=1.0, alias="TELEMETRY_TRACES_SAMPLE_RATE", ge=0.0, le=1.0
    )
    # Push OTel-native metrics (e.g. HTTP server/client histograms from
    # auto-instrumentation) to the collector via OTLP. Independent of the
    # Prometheus `/metrics` scrape endpoint, which is always available.
    telemetry_metrics_enabled: bool = Field(
        default=False, alias="TELEMETRY_METRICS_ENABLED"
    )
    # Ship log records to the collector over OTLP, in addition to (never
    # instead of) the stdout logging that `kubectl logs` shows. The structlog
    # chain already stamps trace_id/span_id on every entry; exporting the
    # records hands the backend that correlation as structured fields rather
    # than something to re-parse out of a scraped file.
    telemetry_logs_enabled: bool = Field(default=False, alias="TELEMETRY_LOGS_ENABLED")
    # Also export spans/metrics/logs to stdout (debugging the pipeline locally).
    telemetry_console_export: bool = Field(
        default=False, alias="TELEMETRY_CONSOLE_EXPORT"
    )
    # Deployment environment tag attached to every span/metric as the
    # `deployment.environment` resource attribute (dev/staging/production).
    deployment_environment: str = Field(
        default="development",
        validation_alias=AliasChoices("DEPLOYMENT_ENVIRONMENT", "ENVIRONMENT"),
    )
    # Service version reported as the `service.version` resource attribute.
    # Defaults to the installed package version.
    service_version: str = Field(
        default_factory=lambda: _resolve_service_version(),
        alias="SERVICE_VERSION",
    )
    # A Sentry DSN embeds a project ingest key; wrap it per the SecretStr
    # credential rule so it never leaks via repr()/model_dump()/logs.
    sentry_dsn: SecretStr | None = Field(default=None, alias="SENTRY_DSN")
    # Sentry trace/profile sample rates. Defaults are conservative for
    # production; raise to 1.0 only in pre-prod or for short investigations.
    sentry_traces_sample_rate: float = Field(
        default=0.1, alias="SENTRY_TRACES_SAMPLE_RATE", ge=0.0, le=1.0
    )
    # Profiling is an investigation tool, not steady state: the profiler samples
    # the interpreter at ~100 Hz for every profiled transaction, which is real
    # CPU on a pod sized around one core. Off by default; raise it for the
    # duration of an investigation and put it back.
    sentry_profiles_sample_rate: float = Field(
        default=0.0, alias="SENTRY_PROFILES_SAMPLE_RATE", ge=0.0, le=1.0
    )

    # === Feature Flags ===
    # Include test cases generation in the project planner agent.
    project_planner_enable_test_cases: bool = Field(
        default=True, alias="PROJECT_PLANNER_ENABLE_TEST_CASES"
    )

    # === Timezone ===
    app_timezone: str = Field(default="Europe/Rome", alias="APP_TIMEZONE")

    @property
    def timezone(self) -> ZoneInfo:
        """Helper to get a validated ZoneInfo object."""
        try:
            return ZoneInfo(self.app_timezone)
        except Exception:
            return ZoneInfo("UTC")

    # === Feedback Loops ===
    # Enable user feedback collection for reinforcement learning.
    enable_feedback: bool = Field(default=True, alias="ENABLE_FEEDBACK")
    feedback_boost_enabled: bool = Field(default=True, alias="FEEDBACK_BOOST_ENABLED")
    feedback_positive_weight: float = Field(
        default=0.05, alias="FEEDBACK_POSITIVE_WEIGHT"
    )
    feedback_negative_weight: float = Field(
        default=0.1, alias="FEEDBACK_NEGATIVE_WEIGHT"
    )
    feedback_score_min_total: int = Field(
        default=3, alias="FEEDBACK_SCORE_MIN_TOTAL", ge=0
    )
    # Default lookback window (days) applied to feedback analytics when no
    # explicit range is requested, so queries never scan the full table.
    feedback_analytics_default_days: int = Field(
        default=90, alias="FEEDBACK_ANALYTICS_DEFAULT_DAYS", ge=1
    )
    # Hard cap on feedback rows pulled for in-Python document aggregation.
    feedback_analytics_doc_scan_limit: int = Field(
        default=10000, alias="FEEDBACK_ANALYTICS_DOC_SCAN_LIMIT", ge=1
    )
    # TTL (seconds) of the in-process document feedback rollup cache. The
    # rollup scans up to feedback_analytics_doc_scan_limit rows and aggregates
    # them in Python on *every* RAG request when FEEDBACK_BOOST_ENABLED is on,
    # so it is cached briefly rather than recomputed per request. Set to 0 to
    # disable caching and always recompute.
    feedback_summary_cache_ttl: float = Field(
        default=60.0, alias="FEEDBACK_SUMMARY_CACHE_TTL", ge=0.0
    )

    # === Active Learning ===
    active_learning_min_total: int = Field(
        default=4, alias="ACTIVE_LEARNING_MIN_TOTAL", ge=1
    )
    active_learning_max_positive_rate: float = Field(
        default=0.6, alias="ACTIVE_LEARNING_MAX_POSITIVE_RATE", ge=0.0, le=1.0
    )
    active_learning_limit: int = Field(default=20, alias="ACTIVE_LEARNING_LIMIT", ge=1)

    # === Cost Control ===
    cost_control_enabled: bool = Field(
        default=True,
        validation_alias=AliasChoices("COST_CONTROL_ENABLED", "LLM_BUDGET_ENABLED"),
    )
    # Global cap on tokens per agent run to prevent infinite loops/runaway costs.
    agent_max_tokens: int = Field(
        default=10000,
        validation_alias=AliasChoices("AGENT_MAX_TOKENS", "LLM_BUDGET_MAX_TOKENS"),
        ge=100,
        description=(
            "Per-agent-run runaway cap, not a monthly budget: one chat run that "
            "burns more than this is aborted by CostControlMiddleware."
        ),
    )

    # === Caching (Logic limits) ===
    # Note: Database backend connection details are in `StorageConfig`
    chat_response_cache_enabled: bool = Field(
        default=True, alias="CHAT_RESPONSE_CACHE_ENABLED"
    )
    chat_response_cache_ttl: float = Field(
        default=3600.0, alias="CHAT_RESPONSE_CACHE_TTL"
    )
    chat_response_cache_maxsize: int = Field(
        default=256, alias="CHAT_RESPONSE_CACHE_MAXSIZE", ge=1
    )

    # Pre-retrieval answer cache. Keyed WITHOUT the retrieved context, so a
    # hit skips vector search + cross-encoder rerank + context building. That
    # key cannot observe a corpus change through the query alone, so the
    # feature is opt-in, namespaced apart from the response cache and given a
    # deliberately short TTL. See docs/core-modules/chat.md.
    chat_rag_precheck_enabled: bool = Field(
        default=False,
        alias="CHAT_RAG_PRECHECK_ENABLED",
        description=(
            "Pre-retrieval answer cache, probed right after history load and "
            "keyed WITHOUT the retrieved context, so a hit skips vector search, "
            "cross-encoder rerank and context building — not just generation. "
            "That key cannot observe a corpus change through the query alone: "
            "the indexing service's index_version is folded in, which "
            "invalidates on an in-process reindex, but a reindex performed by "
            "ANOTHER process is invisible here and the short TTL is the only "
            "defence. Leave it off unless answers up to "
            "CHAT_RAG_PRECHECK_TTL seconds stale are acceptable. Keys live in "
            "their own namespace and flush independently of the response cache. "
            "See docs/core-modules/chat.md."
        ),
    )
    chat_rag_precheck_ttl: float = Field(
        default=60.0, alias="CHAT_RAG_PRECHECK_TTL", gt=0
    )
    chat_rag_precheck_maxsize: int = Field(
        default=256, alias="CHAT_RAG_PRECHECK_MAXSIZE", ge=1
    )

    chat_rerank_cache_enabled: bool = Field(
        default=True, alias="CHAT_RERANK_CACHE_ENABLED"
    )
    chat_rerank_cache_ttl: float = Field(default=600.0, alias="CHAT_RERANK_CACHE_TTL")
    chat_rerank_cache_maxsize: int = Field(
        default=4096, alias="CHAT_RERANK_CACHE_MAXSIZE", ge=1
    )

    analysis_cache_enabled: bool = Field(
        default=True,
        alias="ANALYSIS_CACHE_ENABLED",
        description="Deprecated, no effect: nothing reads it",
    )
    analysis_cache_ttl: float = Field(
        default=86400.0,
        alias="ANALYSIS_CACHE_TTL",
        description="Deprecated, no effect: nothing reads it",
    )
    analysis_cache_maxsize: int = Field(
        default=128,
        alias="ANALYSIS_CACHE_MAXSIZE",
        ge=1,
        description="Deprecated, no effect: nothing reads it",
    )

    # === Chat Memory ===
    chat_memory_enabled: bool = Field(default=True, alias="CHAT_MEMORY_ENABLED")
    chat_memory_ttl: float = Field(default=3600.0, alias="CHAT_MEMORY_TTL")
    # Max previous turns to include in the context window.
    chat_memory_max_turns: int = Field(default=6, alias="CHAT_MEMORY_MAX_TURNS", ge=1)
    chat_memory_max_sessions: int = Field(
        default=1024, alias="CHAT_MEMORY_MAX_SESSIONS", ge=1
    )
    # Enable automatic summarization of long conversations.
    chat_memory_summary_enabled: bool = Field(
        default=True, alias="CHAT_MEMORY_SUMMARY_ENABLED"
    )
    chat_memory_summary_max_turns: int = Field(
        default=8, alias="CHAT_MEMORY_SUMMARY_MAX_TURNS", ge=0
    )
    chat_memory_summary_max_chars: int = Field(
        default=800, alias="CHAT_MEMORY_SUMMARY_MAX_CHARS", ge=120
    )

    # === Guardrails ===
    # Wall-clock cap on a single ``POST /chat/stream`` SSE response. A hung or
    # very slow provider otherwise holds the connection — and the worker slot
    # behind it — open indefinitely; on expiry the stream is closed cleanly with
    # its terminal ``event: done`` frame rather than dropped mid-token.
    chat_stream_timeout_seconds: float = Field(
        default=300.0, alias="CHAT_STREAM_TIMEOUT_SECONDS", gt=0
    )

    chat_guardrails_enabled: bool = Field(default=True, alias="CHAT_GUARDRAILS_ENABLED")
    chat_guardrails_block_message: str = Field(
        default="I cannot assist you with this request.",
        alias="CHAT_GUARDRAILS_BLOCK_MESSAGE",
    )
    chat_guardrails_out_of_scope_message: str = Field(
        default="I can only answer questions related to indexed documents.",
        alias="CHAT_GUARDRAILS_OUT_OF_SCOPE_MESSAGE",
    )
    # List of prohibited keywords (Regex supported). NoDecode + csv_list so a
    # comma-separated (or blank) value parses instead of raising a
    # SettingsError out of the entire AppConfig — see
    # :mod:`core.config._collections`.
    chat_guardrails_block_keywords: Annotated[list[str], NoDecode] = Field(
        default_factory=list, alias="CHAT_GUARDRAILS_BLOCK_KEYWORDS"
    )
    # Patterns to detect off-topic queries.
    chat_guardrails_out_of_scope_patterns: Annotated[list[str], NoDecode] = Field(
        default_factory=list, alias="CHAT_GUARDRAILS_OUT_OF_SCOPE_PATTERNS"
    )

    @field_validator(
        "chat_guardrails_block_keywords",
        "chat_guardrails_out_of_scope_patterns",
        mode="before",
    )
    @classmethod
    def _parse_csv_lists(cls, value: Any) -> Any:
        """Accept ``a,b`` and a blank value, as well as a JSON array."""
        return csv_list(value)

    @field_validator("root_redirect", mode="before")
    @classmethod
    def _validate_root_redirect(cls, value: Any) -> Any:
        """Confine the landing to this site, and refuse a self-redirect.

        The value lands verbatim in a ``Location`` header on an unauthenticated
        route, so anything but a site-relative path turns the root into an open
        redirect — a phishing primitive that borrows the deployment's own
        hostname. Rejected: absolute URLs (``https://evil.example``), the
        protocol-relative form (``//evil.example``, a host — not a path), a
        backslash (browsers normalise ``/\\evil.example`` to ``//evil.example``),
        embedded CR/LF (header splitting) and ``/`` itself (a redirect loop).

        Raising here fails the boot with the offending value named, rather than
        serving a root that quietly points off-site.
        """
        if value is None:
            return ""
        if not isinstance(value, str):
            return value
        target = value.strip()
        if not target:
            return ""
        problem: str | None = None
        if not target.startswith("/"):
            problem = "must start with '/' (site-relative path)"
        elif target.startswith("//") or target.startswith("/\\"):
            problem = "names a host, not a path (open redirect)"
        elif "\\" in target:
            problem = "must not contain a backslash (browsers read it as '/')"
        elif any(char < " " or char == "\x7f" for char in target):
            problem = "must not contain control characters"
        elif target == "/":
            problem = "would redirect '/' to itself"
        if problem is not None:
            raise ValueError(f"BASELITH_ROOT_REDIRECT {problem}: {target!r}")
        return target


# Internal singleton for app configuration.
_app_config: AppConfig | None = None


def get_app_config() -> AppConfig:
    """
    Retrieve or initialize the global AppConfig instance.

    Returns:
        AppConfig: The singleton application configuration.
    """
    global _app_config
    if _app_config is None:
        _app_config = AppConfig()
        logger.info(f"Initialized AppConfig (timezone={_app_config.app_timezone})")
    return _app_config

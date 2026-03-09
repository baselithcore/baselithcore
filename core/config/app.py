"""
General Application Configuration for BaselithCore.

This module defines high-level settings that govern the runtime behavior of
the application, including server parameters, multi-tenancy rules,
observability (logging/telemetry), cost controls, and safety guardrails.
"""

import logging
from typing import Optional, List

from pydantic import Field, AliasChoices
from pydantic_settings import BaseSettings, SettingsConfigDict
from zoneinfo import ZoneInfo

logger = logging.getLogger(__name__)


class AppConfig(BaseSettings):
    """
    Main application configuration schema.

    Settings are loaded from environment variables (case-insensitive)
    or a `.env` file.
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # === Server & Network ===
    # Network interface to bind the application server to.
    host: str = Field(default="0.0.0.0", alias="HOST")  # nosec B104
    # Port to listen on.
    port: int = Field(default=8000, alias="PORT")

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
    # OpenTelemetry collector endpoint for traces and metrics.
    telemetry_otel_endpoint: str = Field(
        default="http://localhost:4317", alias="TELEMETRY_OTEL_ENDPOINT"
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

    chat_rerank_cache_enabled: bool = Field(
        default=True, alias="CHAT_RERANK_CACHE_ENABLED"
    )
    chat_rerank_cache_ttl: float = Field(default=600.0, alias="CHAT_RERANK_CACHE_TTL")
    chat_rerank_cache_maxsize: int = Field(
        default=4096, alias="CHAT_RERANK_CACHE_MAXSIZE", ge=1
    )

    analysis_cache_enabled: bool = Field(default=True, alias="ANALYSIS_CACHE_ENABLED")
    analysis_cache_ttl: float = Field(default=86400.0, alias="ANALYSIS_CACHE_TTL")
    analysis_cache_maxsize: int = Field(
        default=128, alias="ANALYSIS_CACHE_MAXSIZE", ge=1
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
    chat_guardrails_enabled: bool = Field(default=True, alias="CHAT_GUARDRAILS_ENABLED")
    chat_guardrails_block_message: str = Field(
        default="I cannot assist you with this request.",
        alias="CHAT_GUARDRAILS_BLOCK_MESSAGE",
    )
    chat_guardrails_out_of_scope_message: str = Field(
        default="I can only answer questions related to indexed documents.",
        alias="CHAT_GUARDRAILS_OUT_OF_SCOPE_MESSAGE",
    )
    # List of prohibited keywords (Regex supported).
    chat_guardrails_block_keywords: List[str] = Field(
        default_factory=list, alias="CHAT_GUARDRAILS_BLOCK_KEYWORDS"
    )
    # Patterns to detect off-topic queries.
    chat_guardrails_out_of_scope_patterns: List[str] = Field(
        default_factory=list, alias="CHAT_GUARDRAILS_OUT_OF_SCOPE_PATTERNS"
    )


# Internal singleton for app configuration.
_app_config: Optional[AppConfig] = None


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

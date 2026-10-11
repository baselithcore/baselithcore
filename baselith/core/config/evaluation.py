"""Evaluation configuration (``EVAL_``).

Judge model, dataset location and thresholds for the trajectory-aware case
evaluation and the CI replay runner, plus the online evaluation of production
traffic (``EVAL_ONLINE_*``): sampling, the opt-in LLM judge rate, the rolling
quality monitor (per process, or fleet-wide through Redis), the webhook tenant
its transitions go to, and the opt-in result store.
"""

from typing import Literal

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

from baselith.core.config.overrides import overridable


class EvaluationConfig(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="EVAL_")

    enabled: bool = False
    # SecretStr so a repr()/log/Sentry capture of the settings object can
    # never print the key (project-wide credential rule).
    openai_api_key: SecretStr | None = None
    model: str = "gpt-4-turbo-preview"  # Default evaluator model
    # LLM-as-judge scoring is nondeterministic: one sample per case makes the
    # verdict a coin flip, so the scheduled run scores each case k times and
    # gates on the median (EVAL_JUDGE_SAMPLES).
    judge_samples: int = Field(
        default=3,
        ge=1,
        description="Judge evaluations per case; the median score gates.",
    )
    judge_max_parallel: int = Field(
        default=4,
        ge=1,
        description="Maximum judge calls in flight at once across the suite.",
    )

    # --- Online evaluation of production traffic --------------------------
    # Requires EVAL_ENABLED as well; this flag starts the service at boot.
    online_enabled: bool = Field(
        default=False,
        description="Start online evaluation of production flows at boot "
        "(also needs EVAL_ENABLED).",
    )
    online_sample_rate: float = Field(
        default=0.05,
        ge=0.0,
        le=1.0,
        description="Fraction of successful flows sampled for the deterministic "
        "online checks; the decision is a stable hash of the run id.",
    )
    online_intent_sample_rates: dict[str, float] = Field(
        default_factory=dict,
        description="Per-intent sample-rate overrides, as JSON "
        '(e.g. {"qa_docs": 0.2}).',
    )
    online_judge_rate: float = Field(
        default=0.0,
        ge=0.0,
        le=1.0,
        description="Fraction of all flows also scored by the LLM judge, capped "
        "at the sample rate. 0 keeps the judge off.",
    )
    online_queue_size: int = Field(
        default=256,
        ge=1,
        description="Sampled flows waiting for evaluation; beyond it new "
        "samples are dropped and counted, never awaited.",
    )
    online_workers: int = Field(
        default=2,
        ge=1,
        description="Concurrent online evaluations (and judge calls) in flight.",
    )
    online_min_answer_chars: int = Field(
        default=20,
        ge=0,
        description="Answers shorter than this fail the too-short check.",
    )
    online_max_intents: int = Field(
        default=50,
        ge=1,
        description="Distinct intent label values on the online metrics; "
        "further intents are reported as 'other'.",
    )
    online_window: int = Field(
        default=50,
        ge=1,
        description="Recent scores per intent in the quality monitor's window.",
    )
    online_reference_window: int = Field(
        default=500,
        ge=1,
        description="Scores per intent in the trailing reference window "
        "(must exceed EVAL_ONLINE_WINDOW to be used).",
    )
    online_min_samples: int = Field(
        default=20,
        ge=1,
        description="Scores a window needs before the monitor judges it, and "
        "between two state changes.",
    )
    online_quality_floor: float | None = Field(
        default=None,
        ge=0.0,
        le=1.0,
        description="Absolute floor for the windowed mean score; unset "
        "compares against the reference window only.",
    )
    online_degradation_threshold: float = Field(
        default=0.15,
        gt=0.0,
        le=1.0,
        description="Drop of the windowed mean below the reference mean that "
        "marks an intent degraded.",
    )
    online_recovery_margin: float = Field(
        default=0.05,
        ge=0.0,
        le=1.0,
        description="Hysteresis: a degraded intent recovers only this far "
        "inside the degradation boundary.",
    )
    online_fleet: bool = Field(
        default=True,
        description="Monitor quality across all workers through Redis when "
        "CACHE_BACKEND=redis (one elected worker announces); off, or without "
        "Redis, each worker monitors its own traffic.",
    )
    online_fleet_interval_seconds: float = Field(
        default=15.0,
        gt=0.0,
        description="Seconds between two fleet monitor ticks; the elected "
        "worker's lease expires after three missed ticks.",
    )
    online_webhook_tenant: str = Field(
        default="default",
        min_length=1,
        description="Tenant whose webhook endpoints receive evaluation.degraded "
        "and evaluation.recovered (the operators' tenant).",
    )
    online_store: Literal["none", "memory", "postgres"] = Field(
        default="none",
        description="Where online evaluation results are persisted.",
    )
    online_store_max_records: int = Field(
        default=10_000,
        ge=1,
        description="Records kept by the in-memory store (oldest evicted).",
    )
    online_store_raw_text: bool = Field(
        default=False,
        description="Also store the redacted query and response text.",
    )
    online_retention_days: int = Field(
        default=30,
        ge=0,
        description="Stored online results older than this are swept; 0 keeps "
        "them forever.",
    )

    @property
    def is_enabled(self) -> bool:
        return self.enabled


# Global instance
_evaluation_config: EvaluationConfig | None = None


@overridable(EvaluationConfig)
def get_evaluation_config() -> EvaluationConfig:
    """Get or create global Evaluation config."""
    global _evaluation_config
    if _evaluation_config is None:
        _evaluation_config = EvaluationConfig()
    return _evaluation_config


# Import-time alias of the singleton, kept for existing importers. Code that
# must honour settings swapped after import (the online EvaluationService, the
# runtime-service wiring) calls get_evaluation_config() at use time instead.
evaluation_config = get_evaluation_config()

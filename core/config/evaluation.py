"""Evaluation configuration (``EVAL_``).

Judge model, dataset location and thresholds for the trajectory-aware case
evaluation and the CI replay runner.
"""

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


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

    @property
    def is_enabled(self) -> bool:
        return self.enabled


# Global instance
_evaluation_config: EvaluationConfig | None = None


def get_evaluation_config() -> EvaluationConfig:
    """Get or create global Evaluation config."""
    global _evaluation_config
    if _evaluation_config is None:
        _evaluation_config = EvaluationConfig()
    return _evaluation_config


evaluation_config = get_evaluation_config()

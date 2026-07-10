from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class EvaluationConfig(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="EVAL_")

    enabled: bool = False
    # SecretStr so a repr()/log/Sentry capture of the settings object can
    # never print the key (project-wide credential rule).
    openai_api_key: SecretStr | None = None
    model: str = "gpt-4-turbo-preview"  # Default evaluator model

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

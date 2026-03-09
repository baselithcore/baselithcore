from pydantic_settings import BaseSettings, SettingsConfigDict
from typing import Optional


class EvaluationConfig(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="EVAL_")

    enabled: bool = False
    openai_api_key: Optional[str] = None
    model: str = "gpt-4-turbo-preview"  # Default evaluator model

    @property
    def is_enabled(self) -> bool:
        return self.enabled


# Global instance
_evaluation_config: Optional[EvaluationConfig] = None


def get_evaluation_config() -> EvaluationConfig:
    """Get or create global Evaluation config."""
    global _evaluation_config
    if _evaluation_config is None:
        _evaluation_config = EvaluationConfig()
    return _evaluation_config


evaluation_config = get_evaluation_config()

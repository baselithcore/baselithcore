from pydantic import Field
from pydantic_settings import BaseSettings


class RouterConfig(BaseSettings):
    """Configuration for the Router."""

    model_config = {"env_prefix": "ROUTER_"}

    score_threshold: float = Field(
        default=0.7, description="Minimum similarity score for tool retrieval"
    )
    max_candidates: int = Field(
        default=5, description="Maximum number of agents to return"
    )
    retrieval_limit: int = Field(
        default=50,
        description="Number of entities to retrieve from vector store (N >> K)",
    )


class OrchestrationConfig(BaseSettings):
    """Configuration for the Orchestrator."""

    model_config = {"env_prefix": "ORCHESTRATOR_"}

    default_intent: str = Field(
        default="qa_docs", description="Default intent when classification fails"
    )
    enable_telemetry: bool = Field(
        default=False, description="Whether to record telemetry metrics"
    )
    confidence_threshold: float = Field(
        default=0.6, description="Minimum confidence for LLM classification"
    )


_router_config: RouterConfig | None = None
_orchestration_config: OrchestrationConfig | None = None


def get_router_config() -> RouterConfig:
    """Get router configuration."""
    global _router_config
    if _router_config is None:
        _router_config = RouterConfig()
    return _router_config


def get_orchestration_config() -> OrchestrationConfig:
    """Get orchestration configuration."""
    global _orchestration_config
    if _orchestration_config is None:
        _orchestration_config = OrchestrationConfig()
    return _orchestration_config

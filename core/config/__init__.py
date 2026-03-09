"""
Configuration management package for the BaselithCore framework.

This package provides a unified interface to access all framework settings.
It leverages Pydantic for strict type validation and environment variable
mapping (prefixed with `CORE_`, `APP_`, etc.).

The configuration is split into domain-specific modules (services, storage, security)
to maintain modularity and prevent a monolithic configuration file.
"""

from core.config.base import CoreConfig, get_core_config
from core.config.services import (
    LLMConfig,
    VectorStoreConfig,
    ChatConfig,
    get_llm_config,
    get_vectorstore_config,
    get_chat_config,
    VisionConfig,
    VoiceConfig,
    FineTuningConfig,
    get_vision_config,
    get_voice_config,
    get_finetuning_config,
)
from core.config.plugins import PluginConfig, get_plugin_config
from core.config.storage import StorageConfig, get_storage_config
from core.config.security import SecurityConfig, get_security_config
from core.config.processing import ProcessingConfig, get_processing_config
from core.config.app import AppConfig, get_app_config
from core.config.resilience import ResilienceConfig, get_resilience_config
from core.config.sandbox import SandboxConfig, get_sandbox_config
from core.config.reasoning import ReasoningConfig, get_reasoning_config
from core.config.evaluation import EvaluationConfig, evaluation_config
from core.config.events import EventsConfig, get_events_config
from core.config.mcp import MCPConfig, get_mcp_config
from core.config.prioritization import PrioritizationConfig
from core.config.scraper import ScraperConfig, get_scraper_config
from core.config.swarm import (
    SwarmConfig,
    AuctionConfig,
    TeamConfig,
    get_swarm_config,
)
from core.config.task_queue import TaskQueueConfig
from core.config.world_model import WorldModelConfig, get_world_model_config
from core.config.cache import (
    CacheConfig,
    RedisCacheConfig,
    SemanticCacheConfig,
    get_cache_config,
    get_redis_cache_config,
    get_semantic_cache_config,
)
from core.config.orchestration import (
    RouterConfig,
    OrchestrationConfig,
    get_router_config,
    get_orchestration_config,
)

__all__ = [
    "CoreConfig",
    "get_core_config",
    "LLMConfig",
    "VectorStoreConfig",
    "ChatConfig",
    "get_llm_config",
    "get_vectorstore_config",
    "get_chat_config",
    "VisionConfig",
    "VoiceConfig",
    "FineTuningConfig",
    "get_vision_config",
    "get_voice_config",
    "get_finetuning_config",
    "PluginConfig",
    "get_plugin_config",
    "StorageConfig",
    "get_storage_config",
    "SecurityConfig",
    "get_security_config",
    "ProcessingConfig",
    "get_processing_config",
    "AppConfig",
    "get_app_config",
    "ResilienceConfig",
    "get_resilience_config",
    "SandboxConfig",
    "get_sandbox_config",
    "ReasoningConfig",
    "get_reasoning_config",
    "EvaluationConfig",
    "evaluation_config",
    "EventsConfig",
    "get_events_config",
    "MCPConfig",
    "get_mcp_config",
    "PrioritizationConfig",
    "ScraperConfig",
    "get_scraper_config",
    "SwarmConfig",
    "AuctionConfig",
    "TeamConfig",
    "get_swarm_config",
    "TaskQueueConfig",
    "WorldModelConfig",
    "get_world_model_config",
    "CacheConfig",
    "RedisCacheConfig",
    "SemanticCacheConfig",
    "get_cache_config",
    "get_redis_cache_config",
    "get_semantic_cache_config",
    "RouterConfig",
    "OrchestrationConfig",
    "get_router_config",
    "get_orchestration_config",
]

# Task queue configuration is handled as a singleton internal to the package
_task_queue_config: TaskQueueConfig | None = None


def get_task_queue_config() -> TaskQueueConfig:
    """
    Retrieve the singleton TaskQueueConfig instance.

    Returns:
        TaskQueueConfig: The initialized task queue configuration.
    """
    global _task_queue_config
    if _task_queue_config is None:
        _task_queue_config = TaskQueueConfig()
    return _task_queue_config

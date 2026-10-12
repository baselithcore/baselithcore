"""
Configuration management package for the BaselithCore framework.

This package provides a unified interface to access all framework settings.
It leverages Pydantic for strict type validation and environment variable
mapping (prefixed with `CORE_`, `APP_`, etc.).

The configuration is split into domain-specific modules (services, storage, security)
to maintain modularity and prevent a monolithic configuration file.
"""

# Importing baselith.core.config.env loads the repository .env into os.environ exactly
# once, BEFORE any BaseSettings class is imported/instantiated (some
# instantiate at import time). Individual config classes no longer declare
# env_file — see baselith.core.config.env.load_project_env.
from baselith.core.config import env as _env
from baselith.core.config.app import AppConfig, get_app_config
from baselith.core.config.base import CoreConfig, get_core_config
from baselith.core.config.cache import (
    CacheConfig,
    RedisCacheConfig,
    SemanticCacheConfig,
    get_cache_config,
    get_redis_cache_config,
    get_semantic_cache_config,
)
from baselith.core.config.concurrency import get_web_concurrency, set_web_concurrency
from baselith.core.config.drift import (
    EnvSuspect,
    register_owned_env,
    suspected_typos,
    warn_on_suspected_typos,
)
from baselith.core.config.environment import get_runtime_environment, is_production_env
from baselith.core.config.evaluation import EvaluationConfig, evaluation_config
from baselith.core.config.events import EventsConfig, get_events_config
from baselith.core.config.mcp import MCPConfig, MCPServerSpec, get_mcp_config
from baselith.core.config.memory import (
    MemoryRuntimeConfig,
    SupermemoryConfig,
    get_memory_runtime_config,
    get_supermemory_config,
)
from baselith.core.config.orchestration import (
    OrchestrationConfig,
    RouterConfig,
    get_orchestration_config,
    get_router_config,
)
from baselith.core.config.overrides import overridable, override_config
from baselith.core.config.plugin_update_apply import (
    UpdateApplyConfig,
    get_update_apply_config,
)
from baselith.core.config.plugin_updates import (
    PluginUpdateConfig,
    get_plugin_update_config,
)
from baselith.core.config.plugins import PluginConfig, get_plugin_config
from baselith.core.config.prioritization import PrioritizationConfig
from baselith.core.config.processing import ProcessingConfig, get_processing_config
from baselith.core.config.reasoning import ReasoningConfig, get_reasoning_config
from baselith.core.config.resilience import ResilienceConfig, get_resilience_config
from baselith.core.config.sandbox import SandboxConfig, get_sandbox_config
from baselith.core.config.scraper import ScraperConfig, get_scraper_config
from baselith.core.config.security import SecurityConfig, get_security_config
from baselith.core.config.services import (
    ChatConfig,
    FineTuningConfig,
    LLMConfig,
    VectorStoreConfig,
    VisionConfig,
    VoiceConfig,
    get_chat_config,
    get_finetuning_config,
    get_llm_config,
    get_vectorstore_config,
    get_vision_config,
    get_voice_config,
)
from baselith.core.config.storage import StorageConfig, get_storage_config
from baselith.core.config.swarm import (
    AuctionConfig,
    SwarmConfig,
    TeamConfig,
    get_swarm_config,
)
from baselith.core.config.task_queue import TaskQueueConfig
from baselith.core.config.webhooks import WebhookConfig, get_webhook_config
from baselith.core.config.world_model import WorldModelConfig, get_world_model_config

__all__ = [
    "override_config",
    "AppConfig",
    "AuctionConfig",
    "CacheConfig",
    "ChatConfig",
    "CoreConfig",
    "EnvSuspect",
    "EvaluationConfig",
    "EventsConfig",
    "FineTuningConfig",
    "LLMConfig",
    "MCPConfig",
    "MCPServerSpec",
    "OrchestrationConfig",
    "PluginConfig",
    "PluginUpdateConfig",
    "UpdateApplyConfig",
    "PrioritizationConfig",
    "ProcessingConfig",
    "ReasoningConfig",
    "RedisCacheConfig",
    "ResilienceConfig",
    "RouterConfig",
    "SandboxConfig",
    "ScraperConfig",
    "SecurityConfig",
    "SemanticCacheConfig",
    "StorageConfig",
    "MemoryRuntimeConfig",
    "SupermemoryConfig",
    "SwarmConfig",
    "TaskQueueConfig",
    "TeamConfig",
    "VectorStoreConfig",
    "VisionConfig",
    "VoiceConfig",
    "WebhookConfig",
    "WorldModelConfig",
    "evaluation_config",
    "get_app_config",
    "get_cache_config",
    "get_chat_config",
    "get_core_config",
    "get_events_config",
    "get_finetuning_config",
    "get_llm_config",
    "get_mcp_config",
    "get_orchestration_config",
    "get_plugin_config",
    "get_plugin_update_config",
    "get_update_apply_config",
    "get_processing_config",
    "get_reasoning_config",
    "get_redis_cache_config",
    "get_resilience_config",
    "get_router_config",
    "get_runtime_environment",
    "get_sandbox_config",
    "get_scraper_config",
    "get_security_config",
    "get_semantic_cache_config",
    "get_storage_config",
    "get_memory_runtime_config",
    "get_supermemory_config",
    "get_web_concurrency",
    "set_web_concurrency",
    "register_owned_env",
    "suspected_typos",
    "warn_on_suspected_typos",
    "get_swarm_config",
    "get_vectorstore_config",
    "get_vision_config",
    "get_voice_config",
    "get_webhook_config",
    "get_world_model_config",
    "is_production_env",
]

# Task queue configuration is handled as a singleton internal to the package
_task_queue_config: TaskQueueConfig | None = None


@overridable(TaskQueueConfig)
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

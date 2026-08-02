"""
LLM Service package.

Provides a modular, protocol-based LLM service with support for multiple providers.
"""

from core.services.llm.exceptions import BudgetExceededError
from core.services.llm.fallback_runtime import (
    maybe_run_with_fallback,
    parse_fallback_chain,
    reset_fallback_services,
    run_with_fallback,
)
from core.services.llm.governed import (
    GovernedClientConfig,
    resolve_governed_client_config,
)
from core.services.llm.policy import (
    PluginLLMPolicy,
    resolve_plugin_llm_policy,
    set_plugin_llm_policy_resolver,
)
from core.services.llm.service import LLMService, get_llm_service
from core.services.llm.structured import generate_typed
from core.services.llm.tool_calling import (
    ANY,
    AUTO,
    NONE,
    LLMResult,
    LLMToolSpec,
    ResponseFormat,
    ToolCall,
    ToolChoice,
    tool_spec_from_mcp,
)

__all__ = [
    "ANY",
    "AUTO",
    "NONE",
    "BudgetExceededError",
    "GovernedClientConfig",
    "LLMResult",
    "LLMService",
    "LLMToolSpec",
    "PluginLLMPolicy",
    "ResponseFormat",
    "ToolCall",
    "ToolChoice",
    "generate_typed",
    "get_llm_service",
    "maybe_run_with_fallback",
    "parse_fallback_chain",
    "reset_fallback_services",
    "resolve_governed_client_config",
    "run_with_fallback",
    "resolve_plugin_llm_policy",
    "set_plugin_llm_policy_resolver",
    "tool_spec_from_mcp",
]

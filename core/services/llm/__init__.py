"""
LLM Service package.

Provides a modular, protocol-based LLM service with support for multiple providers.
"""

from core.services.llm._telemetry import (
    register_token_sink,
    report_external_usage,
    unregister_token_sink,
)
from core.services.llm.credentials import (
    resolve_llm_credential,
    set_llm_credential_resolver,
)
from core.services.llm.errors import (
    LLMClientError,
    LLMConnectionError,
    LLMRateLimitError,
    LLMRefusalError,
    LLMServerError,
    LLMTimeoutError,
)
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
from core.services.llm.images import GeneratedImage, generate_image
from core.services.llm.messages import (
    ContentBlock,
    ImageBlock,
    Message,
    Role,
    TextBlock,
    ThinkingBlock,
    ToolResultBlock,
    ToolUseBlock,
    message_from_result,
    render_as_prompt,
    to_anthropic,
    to_openai,
)
from core.services.llm.model_capabilities import (
    ModelCapabilities,
    ThinkingMode,
    capabilities_for,
    clamp_effort,
    default_max_tokens,
    rejects_forced_tool_choice,
    supports_sampling_params,
)
from core.services.llm.policy import (
    PluginLLMPolicy,
    resolve_plugin_llm_policy,
    set_plugin_llm_policy_resolver,
)
from core.services.llm.runtime import api_key_from_config
from core.services.llm.service import LLMService, get_llm_service
from core.services.llm.structured import generate_typed
from core.services.llm.thinking import EffortLevel
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
from core.services.llm.usage import Usage

__all__ = [
    "EffortLevel",
    "LLMClientError",
    "LLMConnectionError",
    "LLMRateLimitError",
    "LLMRefusalError",
    "LLMServerError",
    "LLMTimeoutError",
    "ContentBlock",
    "ImageBlock",
    "Message",
    "ModelCapabilities",
    "Role",
    "TextBlock",
    "ThinkingBlock",
    "ThinkingMode",
    "ToolResultBlock",
    "ToolUseBlock",
    "Usage",
    "capabilities_for",
    "clamp_effort",
    "default_max_tokens",
    "rejects_forced_tool_choice",
    "supports_sampling_params",
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
    "GeneratedImage",
    "api_key_from_config",
    "generate_image",
    "generate_typed",
    "get_llm_service",
    "message_from_result",
    "render_as_prompt",
    "to_anthropic",
    "to_openai",
    "maybe_run_with_fallback",
    "parse_fallback_chain",
    "register_token_sink",
    "report_external_usage",
    "unregister_token_sink",
    "reset_fallback_services",
    "resolve_governed_client_config",
    "resolve_llm_credential",
    "run_with_fallback",
    "resolve_plugin_llm_policy",
    "set_llm_credential_resolver",
    "set_plugin_llm_policy_resolver",
    "tool_spec_from_mcp",
]

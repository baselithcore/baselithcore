"""The BaselithCore public API.

Everything a program should need to build on BaselithCore is re-exported
here::

    from baselith import Agent, Crew, Task

The framework's implementation lives under ``core.*``, a package name that
only makes sense from inside this repository. ``baselith.core.agent`` and the rest
keep working exactly as before and are not deprecated; this module is the
name downstream code should import, and the only surface covered by the
``stable`` tier of the [stability
policy](../mkdocs-site/docs/advanced/api-stability.md). A symbol reachable
from here does not move or change shape without a deprecation cycle; a
symbol reachable only from a ``core.*`` package carries the tier its own
package declares.

Attribute access is lazy (:pep:`562`), so ``import baselith`` costs a few
milliseconds and pulls in nothing else. The first time a name is read, its
module is imported and the value cached in the module globals, so later
reads are plain dictionary lookups. That matters because the orchestrator
and the LLM service are eagerly wired: reaching ``Agent`` imports roughly
three thousand modules, and a program that only needs ``SkillResult`` to
type a tool's return value should not pay for them.
"""

from __future__ import annotations

import importlib
from typing import TYPE_CHECKING, Any, Final

from baselith.core._version import __version__

if TYPE_CHECKING:  # pragma: no cover - import-time contract for type checkers
    from baselith.core.agent import (
        Agent,
        AgentOutputValidationError,
        AgentResult,
        AgentUsage,
        CapabilitySelector,
        ChatMessage,
        CostFn,
        Crew,
        CrewResult,
        GroupChat,
        GroupChatResult,
        LLMManagerSelector,
        Participant,
        ReviewDecision,
        ReviewVerdict,
        RoundRobinSelector,
        SpeakerSelector,
        Task,
        TaskResult,
    )
    from baselith.core.api.factory import create_app
    from baselith.core.config import get_app_config, get_core_config
    from baselith.core.orchestration.autonomy import AutonomyPolicy
    from baselith.core.orchestration.contract import AgentContract
    from baselith.core.orchestration.limits import LoopBudget
    from baselith.core.plugins.result import SkillResult, fail, ok, partial
    from baselith.core.reasoning.react_types import ToolDefinition

#: ``exported name -> module that defines it``. The single source of truth
#: for both the lazy loader and ``__all__``; a name absent here is not part
#: of the public API, whatever ``core.*`` exports.
_EXPORTS: Final[dict[str, str]] = {
    # Typed agents and crews — the surface the quickstart teaches.
    "Agent": "baselith.core.agent",
    "AgentOutputValidationError": "baselith.core.agent",
    "AgentResult": "baselith.core.agent",
    "AgentUsage": "baselith.core.agent",
    "CostFn": "baselith.core.agent",
    "Crew": "baselith.core.agent",
    "CrewResult": "baselith.core.agent",
    "Task": "baselith.core.agent",
    "TaskResult": "baselith.core.agent",
    "ReviewDecision": "baselith.core.agent",
    "ReviewVerdict": "baselith.core.agent",
    # Multi-agent conversation.
    "CapabilitySelector": "baselith.core.agent",
    "ChatMessage": "baselith.core.agent",
    "GroupChat": "baselith.core.agent",
    "GroupChatResult": "baselith.core.agent",
    "LLMManagerSelector": "baselith.core.agent",
    "Participant": "baselith.core.agent",
    "RoundRobinSelector": "baselith.core.agent",
    "SpeakerSelector": "baselith.core.agent",
    # Writing tools and skills: the result envelope and the explicit
    # tool declaration a plain callable is inferred into.
    "SkillResult": "baselith.core.plugins.result",
    "ToolDefinition": "baselith.core.reasoning.react_types",
    "fail": "baselith.core.plugins.result",
    "ok": "baselith.core.plugins.result",
    "partial": "baselith.core.plugins.result",
    # Runtime limits: what makes a loop terminate, what it may do
    # unattended, and what it promises its caller.
    "AgentContract": "baselith.core.orchestration.contract",
    "AutonomyPolicy": "baselith.core.orchestration.autonomy",
    "LoopBudget": "baselith.core.orchestration.limits",
    # The server and its configuration.
    "create_app": "baselith.core.api.factory",
    "get_app_config": "baselith.core.config",
    "get_core_config": "baselith.core.config",
}

#: Spelled out rather than derived from :data:`_EXPORTS`, so that the public
#: API surface gate — which reads literal ``__all__`` lists with the AST and
#: never imports — covers this package. ``test_all_matches_exports`` keeps the
#: two in step.
__all__ = [
    "Agent",
    "AgentContract",
    "AgentOutputValidationError",
    "AgentResult",
    "AgentUsage",
    "AutonomyPolicy",
    "CapabilitySelector",
    "ChatMessage",
    "CostFn",
    "Crew",
    "CrewResult",
    "GroupChat",
    "GroupChatResult",
    "LLMManagerSelector",
    "LoopBudget",
    "Participant",
    "ReviewDecision",
    "ReviewVerdict",
    "RoundRobinSelector",
    "SkillResult",
    "SpeakerSelector",
    "Task",
    "TaskResult",
    "ToolDefinition",
    "__version__",
    "create_app",
    "fail",
    "get_app_config",
    "get_core_config",
    "ok",
    "partial",
]


def __getattr__(name: str) -> Any:
    """Import and cache a public symbol on first access (:pep:`562`)."""
    module_name = _EXPORTS.get(name)
    if module_name is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    value = getattr(importlib.import_module(module_name), name)
    globals()[name] = value
    return value


def __dir__() -> list[str]:
    """The public names, so ``dir()`` and tab-completion see the lazy ones."""
    return list(__all__)

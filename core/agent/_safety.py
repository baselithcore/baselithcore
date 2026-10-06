"""Safe defaults for a typed agent running outside an orchestrated request.

``Orchestrator.process`` hands every request a SUPERVISED
:class:`~core.orchestration.autonomy.AutonomyPolicy` and a
:class:`~core.orchestration.limits.LoopBudget`. A standalone
:class:`~core.agent.agent.Agent` (or a ``Crew``/``GroupChat`` of them) had
neither: a tool declared ``destructive`` ran without anyone being asked, and
the run was bounded by ``max_iterations`` alone. The two defaults here close
those gaps while keeping the old behaviour one explicit argument away:

* **destructive-tool guard** — a tool explicitly declared
  ``category="destructive"`` is refused, with an error the model reads and a
  log line that says how to opt in. Plain callables (the quickstart path),
  tools left at the default category and every other category run exactly as
  before. ``autonomy_policy=None`` disables the
  guard; an ``AutonomyPolicy`` replaces it with the real approval gate.
* **default loop budget** — when no budget is ambient, the run gets one built
  from the orchestrator's own defaults (:class:`LoopLimits`), widened so that
  ``max_iterations`` stays the cap that actually governs the loop.
  ``loop_limits=None`` disables it.
"""

from __future__ import annotations

import inspect
from collections.abc import Callable, Sequence
from typing import TYPE_CHECKING, Any, Final

from core.observability.logging import get_logger
from core.orchestration.budget_context import DEFAULT_LIMITS

if TYPE_CHECKING:  # pragma: no cover - typing only
    from core.orchestration.limits import LoopLimits
    from core.reasoning.react import ToolDefinition

logger = get_logger(__name__)

__all__ = [
    "DEFAULT",
    "collect_tools",
    "default_loop_limits",
    "destructive_denial",
    "resolve_loop_limits",
    "shared_loop_limits",
]


#: Default value of ``Agent(autonomy_policy=..., loop_limits=...)``: the safe
#: default, as opposed to an explicit ``None`` (opt out).
DEFAULT: Final[Any] = DEFAULT_LIMITS


def collect_tools(
    tools: Sequence[Callable[..., Any] | ToolDefinition],
) -> dict[str, ToolDefinition]:
    """Normalise an agent's tools into ``ToolDefinition``s keyed by name.

    A plain callable is wrapped with the default (undeclared) category; a
    ``ToolDefinition`` is kept as given. A later entry with the same name
    replaces an earlier one.

    Args:
        tools: Plain callables or explicit ``ToolDefinition``s.

    Returns:
        The tools by name, in declaration order.
    """
    from core.reasoning.react import ToolDefinition

    by_name: dict[str, ToolDefinition] = {}
    for tool in tools:
        definition = (
            tool
            if isinstance(tool, ToolDefinition)
            else ToolDefinition(
                name=tool.__name__,
                fn=tool,
                description=inspect.getdoc(tool) or tool.__name__,
            )
        )
        by_name[definition.name] = definition
    return by_name


def default_loop_limits(max_iterations: int) -> LoopLimits:
    """The budget a standalone run gets when nothing ambient bounds it.

    The orchestrator's defaults (``budget_usd``, ``max_tool_calls``, and the
    ``ORCHESTRATOR_LOOP_MAX_TOKENS`` / ``ORCHESTRATOR_LOOP_MAX_SECONDS``
    settings), with the iteration cap raised to at least the agent's own so a
    run with ``max_iterations=40`` still ends on its own cap rather than on a
    budget it never asked for.

    Args:
        max_iterations: The caller's own iteration cap.

    Returns:
        LoopLimits: The caps to bind.
    """
    from core.orchestration.limits import DEFAULT_MAX_ITERATIONS, LoopLimits

    return LoopLimits(max_iterations=max(DEFAULT_MAX_ITERATIONS, max_iterations))


def shared_loop_limits() -> LoopLimits:
    """The default budget a multi-agent run (group chat, swarm batch) shares.

    Every participant ``Agent.run`` reuses the ambient budget instead of
    binding its own, so per-run counters would be summed across agents: a
    chat of typed agents that each take a few iterations per turn would end
    rounds early on a shared 25-iteration cap. The iteration and tool-call
    caps are therefore lifted (each agent still has its own ``max_iterations``
    and the chat its ``max_rounds``); the dollar, token and wall-clock caps —
    the ones that bound cost — stay the orchestrator's defaults.

    Returns:
        LoopLimits: The caps to bind.
    """
    from core.orchestration.limits import LoopLimits

    return LoopLimits(max_iterations=_UNBOUNDED_COUNT, max_tool_calls=_UNBOUNDED_COUNT)


#: Effectively no cap on a counter (cost caps still apply).
_UNBOUNDED_COUNT: Final[int] = 1_000_000


def resolve_loop_limits(limits: Any, max_iterations: int) -> LoopLimits | None:
    """Turn a ``loop_limits`` argument into the caps to bind, or ``None``.

    Args:
        limits: :data:`DEFAULT`, an explicit ``LoopLimits``, or ``None``.
        max_iterations: The caller's own iteration cap.

    Returns:
        LoopLimits | None: The caps for a fresh budget, or ``None`` to bind
        none.
    """
    if limits is DEFAULT:
        return default_loop_limits(max_iterations)
    return limits  # type: ignore[no-any-return]


def destructive_denial(agent: Any, definition: ToolDefinition) -> str | None:
    """The refusal for a destructive tool under the standalone guard, if any.

    The guard applies only while it is armed (the ``autonomy_policy``
    argument was left at its default) and no host has injected a policy on
    the instance, and only to a tool whose author explicitly declared
    ``category="destructive"`` (:attr:`ToolDefinition.category_declared`). A
    plain callable, or a ``ToolDefinition`` left at the default category, is
    never refused here — it runs exactly as it did before the guard existed.
    Inside an orchestrated request (an ``Agent`` a plugin handler builds) the
    guard stands down too: the host owns approval policy there.

    Args:
        agent: The owning agent.
        definition: The resolved tool.

    Returns:
        str | None: Runtime narration for the model, or ``None`` to proceed.
    """
    from core.orchestration.autonomy import DESTRUCTIVE
    from core.orchestration.budget_context import in_orchestrated_request
    from core.orchestration.tool_output import escape_untrusted_markers

    if not getattr(agent, "_guard_destructive", False):
        return None
    if in_orchestrated_request():
        return None
    if getattr(agent, "_autonomy_policy", None) is not None:
        return None
    if not definition.category_declared:
        return None
    if definition.normalized_category() != DESTRUCTIVE:
        return None
    name = escape_untrusted_markers(definition.name)
    logger.warning(
        "agent_destructive_tool_refused",
        extra={
            "tool": definition.name,
            "hint": "pass autonomy_policy=AutonomyPolicy(...) to gate it, "
            "or autonomy_policy=None to allow it unguarded",
        },
    )
    return (
        f"Error: tool {name!r} is declared destructive and was not run: this "
        "agent has no approval policy, so destructive tools are refused. Do not "
        "retry it; continue without it or tell the user it needs approval. "
        "(Operator: construct the Agent with autonomy_policy=AutonomyPolicy(...) "
        "to gate it through approval, or autonomy_policy=None to allow it.)"
    )

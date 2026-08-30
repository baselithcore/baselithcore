"""Deterministic pre/post hooks around tool invocations.

Prompts can only *suggest* side effects ("always log writes", "lint after
editing"); a hook bus enforces them. Operators register async callables
matched by tool name (fnmatch glob); the enforcement chokepoint dispatches
``pre`` hooks before a gated tool runs, and the executors dispatch ``post``
hooks after an observation is produced.

Semantics are asymmetric by design:

- **pre** hooks may veto: an exception raised by a pre-hook propagates and
  blocks the invocation (fail-closed) — that is how a policy hook says no.
- **post** hooks are observers: exceptions are logged and swallowed, so a
  broken audit/lint hook can never break the agent loop.

Absent registry ⇒ no-op, matching every other primitive consulted by
:mod:`core.orchestration.enforcement`. A process-wide default registry is
available through :func:`get_tool_hook_registry`; a per-request registry can
be placed on the orchestration context under ``tool_hooks`` to override it.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from fnmatch import fnmatch
from typing import Any, Literal

from core.observability.logging import get_logger

logger = get_logger(__name__)

HookPhase = Literal["pre", "post"]


@dataclass(frozen=True)
class ToolHookEvent:
    """What a hook learns about one tool invocation.

    Attributes:
        tool_name: Name of the tool being invoked.
        category: Autonomy category (``read_only`` … ``external_side_effect``).
        phase: ``pre`` (before execution) or ``post`` (after observation).
        tenant_id: Owning tenant, when known.
        args_digest: SHA-256 hex digest of the canonicalized arguments —
            never the raw arguments, which may hold secrets or PII.
        metadata: Phase-specific extras (e.g. ``ok``/``elapsed_ms`` on post).
    """

    tool_name: str
    category: str = "read_only"
    phase: HookPhase = "pre"
    tenant_id: str | None = None
    args_digest: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


ToolHook = Callable[[ToolHookEvent], Awaitable[None]]


class ToolHookRegistry:
    """Matcher-keyed registry of :data:`ToolHook` callables."""

    def __init__(self) -> None:
        self._hooks: dict[HookPhase, list[tuple[str, ToolHook]]] = {
            "pre": [],
            "post": [],
        }

    def register(self, phase: HookPhase, matcher: str, hook: ToolHook) -> None:
        """Attach ``hook`` to every tool whose name matches ``matcher``.

        Args:
            phase: ``pre`` (may veto) or ``post`` (observer).
            matcher: fnmatch glob against the tool name (``*``, ``db_*``).
            hook: Async callable receiving the :class:`ToolHookEvent`.
        """
        self._hooks[phase].append((matcher, hook))

    def _matching(self, phase: HookPhase, tool_name: str) -> list[ToolHook]:
        return [
            hook for matcher, hook in self._hooks[phase] if fnmatch(tool_name, matcher)
        ]

    async def dispatch_pre(self, event: ToolHookEvent) -> None:
        """Run matching pre-hooks; a raising hook blocks the invocation."""
        for hook in self._matching("pre", event.tool_name):
            await hook(event)

    async def dispatch_post(self, event: ToolHookEvent) -> None:
        """Run matching post-hooks; failures are logged, never raised."""
        for hook in self._matching("post", event.tool_name):
            try:
                await hook(event)
            except Exception as exc:
                logger.warning(
                    "tool_post_hook_failed tool=%s error=%s",
                    event.tool_name,
                    exc,
                )


_default_registry: ToolHookRegistry | None = None


def get_tool_hook_registry() -> ToolHookRegistry:
    """Process-wide default registry (lazily created)."""
    global _default_registry
    if _default_registry is None:
        _default_registry = ToolHookRegistry()
    return _default_registry


def reset_tool_hook_registry() -> None:
    """Drop the default registry (tests / reconfiguration)."""
    global _default_registry
    _default_registry = None


__all__ = [
    "HookPhase",
    "ToolHook",
    "ToolHookEvent",
    "ToolHookRegistry",
    "get_tool_hook_registry",
    "reset_tool_hook_registry",
]

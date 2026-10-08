"""Stop a model that keeps asking for the same tool call, and name why a loop ended.

A tool loop that hands the model the same refusal again and again does not
converge: a weak or confused model re-sends the identical call until
``max_iterations`` runs out, burning every round trip and ending in an opaque
``RuntimeError``. :class:`RepeatGuard` is the opt-in brake (``Agent(
max_identical_calls=N)``): the first N identical calls (same tool, same
arguments) run; the next one is *not executed* and the model is told, in the
observation, to answer with what it has; if it asks again anyway the run ends
with :class:`RepeatedToolCallError`.

The two exceptions are :class:`RuntimeError` subclasses on purpose: callers that
caught the plain ``RuntimeError`` keep working, callers that care can tell a
hard stop from a runaway loop and word the message for their user.
"""

from __future__ import annotations

from core.orchestration.call_keys import canonical_arguments
from core.services.llm.tool_calling import ToolCall

__all__ = [
    "AgentIterationLimitError",
    "AgentLoopError",
    "RepeatGuard",
    "RepeatedToolCallError",
]


class AgentLoopError(RuntimeError):
    """The tool loop ended without a final answer.

    Attributes:
        tool_calls_made: Names of the tools called before the loop ended.
    """

    def __init__(
        self, message: str, *, tool_calls_made: list[str] | None = None
    ) -> None:
        super().__init__(message)
        self.tool_calls_made: list[str] = list(tool_calls_made or [])


class AgentIterationLimitError(AgentLoopError):
    """``max_iterations`` round trips ran out before the model answered."""


class RepeatedToolCallError(AgentLoopError):
    """The model kept repeating an identical tool call after being told to stop.

    Attributes:
        tool_name: The tool it kept calling.
    """

    def __init__(
        self, message: str, *, tool_name: str, tool_calls_made: list[str] | None = None
    ) -> None:
        super().__init__(message, tool_calls_made=tool_calls_made)
        self.tool_name = tool_name


class RepeatGuard:
    """Counts identical calls in one run and decides which are blocked.

    Args:
        limit: How many identical calls may run; the next is blocked.
    """

    def __init__(self, limit: int) -> None:
        self._limit = limit
        self._seen: dict[tuple[str, str], int] = {}
        self._blocked = 0

    def check(self, call: ToolCall) -> str | None:
        """Register ``call``; return the observation to give instead when blocked.

        Raises:
            RepeatedToolCallError: A call was blocked before and the model
                insisted on another one.
        """
        key = (call.name, canonical_arguments(call.arguments))
        count = self._seen.get(key, 0)
        if count < self._limit:
            self._seen[key] = count + 1
            return None
        if self._blocked:
            raise RepeatedToolCallError(
                f"The model kept calling {call.name} with the same arguments "
                f"after being told to stop ({self._limit + 1}+ identical calls)",
                tool_name=call.name,
            )
        self._blocked += 1
        return (
            f"error: you already called {call.name} with these same arguments "
            f"{self._limit} times and the result will not change. Do not call it again: "
            "answer now with what you have, or say plainly what you could not do."
        )

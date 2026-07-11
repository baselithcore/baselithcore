"""
ReAct (Reasoning + Acting) Pattern.

Implements the explicit Thought/Action/Observation loop described in
"ReAct: Synergizing Reasoning and Acting in Language Models" (Yao et al., 2023).

The agent alternates between:
  - Thought  : reasoning about the current situation
  - Action   : calling a tool or producing output
  - Observe  : reading the tool's return value

The loop repeats until a Final Answer is produced or ``max_iterations``
is reached. This makes decisions transparent and debuggable — when
something goes wrong you can read the full trace and understand *why*.

Usage::

    from core.reasoning.react import ReActAgent, ToolDefinition

    async def search(query: str) -> str:
        return f"Results for: {query}"

    agent = ReActAgent(
        tools=[ToolDefinition(name="search", fn=search,
                              description="Search the web")],
        max_iterations=5,
    )
    result = await agent.run("What is the population of Tokyo?")
    print(result.final_answer)
    for step in result.trace:
        print(step)
"""

from __future__ import annotations

import asyncio
import inspect
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from core.observability.logging import get_logger
from core.orchestration.tool_output import truncate_tool_output

logger = get_logger(__name__)


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------


class StepType(str, Enum):
    """Type of a single ReAct trace entry."""

    THOUGHT = "thought"
    ACTION = "action"
    OBSERVATION = "observation"
    FINAL_ANSWER = "final_answer"


@dataclass
class TraceStep:
    """
    One entry in the agent's reasoning trace.

    Attributes:
        step_type: The role this entry plays (Thought, Action, …).
        iteration: Loop counter when this step was produced.
        content: The textual content of the step.
        tool_name: Populated when ``step_type`` is ACTION.
        tool_args: Raw arguments string passed to the tool.
    """

    step_type: StepType
    iteration: int
    content: str
    tool_name: str | None = None
    tool_args: str | None = None

    def __str__(self) -> str:
        prefix = self.step_type.value.capitalize()
        if self.step_type is StepType.ACTION and self.tool_name:
            return (
                f"[iter={self.iteration}] {prefix}: {self.tool_name}({self.tool_args})"
            )
        return f"[iter={self.iteration}] {prefix}: {self.content}"


@dataclass
class ReActResult:
    """
    Complete output of a ReAct agent run.

    Attributes:
        final_answer: The answer produced by the agent.
        trace: Ordered list of Thought/Action/Observation steps.
        iterations_used: How many loop iterations were consumed.
        hit_limit: True when the run ended because ``max_iterations`` was reached.
    """

    final_answer: str
    trace: list[TraceStep] = field(default_factory=list)
    iterations_used: int = 0
    hit_limit: bool = False


@dataclass
class ToolDefinition:
    """
    Descriptor for a tool the ReAct agent may call.

    Attributes:
        name: Identifier used in the prompt and parsed from LLM output.
        fn: Callable that executes the tool. May be sync or async.
        description: Short, human-readable explanation for the system prompt.
        parameters: Optional JSON-Schema object describing the tool's
            arguments, used by the native tool-calling loop. When None the
            schema is inferred from ``fn``'s signature.
    """

    name: str
    fn: Callable[..., Any]
    description: str
    parameters: dict[str, Any] | None = None


# ---------------------------------------------------------------------------
# Agent
# ---------------------------------------------------------------------------

_SYSTEM_TEMPLATE = """\
You are an intelligent agent that answers questions by reasoning step by step \
and using the available tools.

For each step you MUST follow this exact format:

Thought: <your reasoning about what to do next>
Action: <tool_name>(<comma-separated args>)
Observation: <you will see the tool result here>
... (repeat Thought/Action/Observation as needed)
Thought: I have enough information to answer.
Final Answer: <your complete, definitive answer>

Available tools:
{tool_descriptions}

Rules:
- Always think before acting.
- Use at most {max_iterations} tool calls in total.
- If you cannot find the answer, say so honestly — never fabricate.
- When you have enough information, write "Final Answer:" on its own line.
"""

_FINAL_ANSWER_RE = re.compile(r"Final Answer:\s*(.*)", re.DOTALL | re.IGNORECASE)
_THOUGHT_RE = re.compile(r"Thought:\s*(.*?)(?=\nAction:|\nFinal Answer:|$)", re.DOTALL)
_ACTION_RE = re.compile(r"Action:\s*(\w+)\(([^)]*)\)", re.IGNORECASE)


class ReActAgent:
    """
    Executes the ReAct (Reasoning + Acting) loop.

    The agent keeps a running conversation log (messages list) and
    sends it to the LLM on each iteration. When the LLM emits
    ``Final Answer:`` the loop terminates. If the maximum iterations are
    consumed without a final answer, the last observation (or a canned
    message) is returned.

    Args:
        tools: List of ToolDefinition objects the agent may call.
        max_iterations: Hard cap on loop iterations. Always set one.
        llm_service: Optional LLM service; auto-resolved when None.
        system_prompt_extra: Extra text appended after the standard system
            prompt — useful for domain-specific instructions.
        tool_timeout: Per-tool-call timeout in seconds. None (default)
            preserves the historical unbounded behavior. Note: a timed-out
            *sync* tool keeps running in its thread — the loop just stops
            waiting for it.
        tool_retries: Extra attempts after a transient failure
            (``ConnectionError``/``OSError``). Timeouts and other exceptions
            are never retried: a tool that hit its deadline will likely hit
            it again, and arbitrary errors may not be side-effect free.
        retry_backoff: Base sleep between attempts, doubled per retry.
        native_tools: Native tool-calling mode. ``True`` forces the
            structured loop (``LLMService.generate(tools=...)`` consuming
            ``LLMResult.tool_calls``), ``False`` forces the legacy
            text-parsing loop, ``None`` (default) auto-detects: native only
            when the service enables it and the provider supports it.
    """

    def __init__(
        self,
        tools: list[ToolDefinition] | None = None,
        max_iterations: int = 5,
        llm_service=None,
        system_prompt_extra: str = "",
        tool_timeout: float | None = None,
        tool_retries: int = 0,
        retry_backoff: float = 0.5,
        native_tools: bool | None = None,
    ) -> None:
        self._tools: dict[str, ToolDefinition] = {t.name: t for t in (tools or [])}
        self.max_iterations = max_iterations
        self._llm_service = llm_service
        self._system_prompt_extra = system_prompt_extra
        self._tool_timeout = tool_timeout
        self._tool_retries = max(0, tool_retries)
        self._retry_backoff = retry_backoff
        self._native_tools = native_tools

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def run(self, query: str) -> ReActResult:
        """
        Execute the ReAct loop for *query*.

        Args:
            query: The user question or task description.

        Returns:
            ReActResult with the final answer and full trace.
        """
        # Lazy import: react_native imports names from this module.
        from core.reasoning.react_native import resolve_native_mode, run_native_loop

        if resolve_native_mode(self):
            return await run_native_loop(self, query)

        trace: list[TraceStep] = []
        messages = self._build_initial_messages(query)

        for iteration in range(1, self.max_iterations + 1):
            llm_output = await self._call_llm(messages)
            logger.debug(
                "ReAct iteration %d/%d — LLM output length=%d",
                iteration,
                self.max_iterations,
                len(llm_output),
            )

            # Extract Thought
            thought_match = _THOUGHT_RE.search(llm_output)
            if thought_match:
                thought_text = thought_match.group(1).strip()
                trace.append(TraceStep(StepType.THOUGHT, iteration, thought_text))

            # Check for Final Answer first
            final_match = _FINAL_ANSWER_RE.search(llm_output)
            if final_match:
                answer = final_match.group(1).strip()
                trace.append(TraceStep(StepType.FINAL_ANSWER, iteration, answer))
                return ReActResult(
                    final_answer=answer,
                    trace=trace,
                    iterations_used=iteration,
                    hit_limit=False,
                )

            # Extract Action and execute tool
            action_match = _ACTION_RE.search(llm_output)
            if action_match:
                tool_name = action_match.group(1).strip()
                tool_args_raw = action_match.group(2).strip()
                trace.append(
                    TraceStep(
                        StepType.ACTION,
                        iteration,
                        f"{tool_name}({tool_args_raw})",
                        tool_name=tool_name,
                        tool_args=tool_args_raw,
                    )
                )

                observation = await self._execute_tool(tool_name, tool_args_raw)
                trace.append(TraceStep(StepType.OBSERVATION, iteration, observation))

                # Append assistant turn + observation to conversation
                messages.append({"role": "assistant", "content": llm_output})
                messages.append(
                    {
                        "role": "user",
                        "content": f"Observation: {observation}\n\nContinue.",
                    }
                )
            else:
                # No action, no final answer — treat entire output as final answer
                logger.warning(
                    "ReAct: no action or final answer in iteration %d. "
                    "Treating LLM output as final answer.",
                    iteration,
                )
                trace.append(
                    TraceStep(StepType.FINAL_ANSWER, iteration, llm_output.strip())
                )
                return ReActResult(
                    final_answer=llm_output.strip(),
                    trace=trace,
                    iterations_used=iteration,
                    hit_limit=False,
                )

        # Max iterations reached without Final Answer
        logger.warning(
            "ReAct hit max_iterations=%d without Final Answer.", self.max_iterations
        )
        last_obs = next(
            (s.content for s in reversed(trace) if s.step_type is StepType.OBSERVATION),
            "Unable to determine a final answer within the iteration budget.",
        )
        return ReActResult(
            final_answer=last_obs,
            trace=trace,
            iterations_used=self.max_iterations,
            hit_limit=True,
        )

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _build_system_prompt(self) -> str:
        tool_descriptions = (
            "\n".join(f"- {t.name}: {t.description}" for t in self._tools.values())
            or "No tools available."
        )

        prompt = _SYSTEM_TEMPLATE.format(
            tool_descriptions=tool_descriptions,
            max_iterations=self.max_iterations,
        )
        if self._system_prompt_extra:
            prompt += f"\n\n{self._system_prompt_extra}"
        return prompt

    def _build_initial_messages(self, query: str) -> list:
        return [
            {"role": "system", "content": self._build_system_prompt()},
            {"role": "user", "content": query},
        ]

    async def _call_llm(self, messages: list) -> str:
        llm = self._get_llm_service()
        if llm is None:
            return "Final Answer: LLM service unavailable."

        # Convert message list to a flat prompt string compatible with LLMService
        prompt = self._messages_to_prompt(messages)
        system_prompt = next(
            (m["content"] for m in messages if m.get("role") == "system"), None
        )
        try:
            return await llm.generate_response(
                prompt=prompt,
                system_prompt=system_prompt,
            )
        except Exception as exc:
            logger.error("ReAct LLM call failed: %s", exc)
            return "Final Answer: An error occurred while processing your request."

    @staticmethod
    def _messages_to_prompt(messages: list) -> str:
        """Flatten a messages list into a single prompt string."""
        parts = []
        for msg in messages:
            role = msg.get("role", "user")
            content = msg.get("content", "")
            if role == "system":
                continue  # passed separately as system_prompt
            parts.append(f"{role.capitalize()}: {content}")
        return "\n\n".join(parts)

    async def _execute_tool(self, name: str, args_raw: str) -> str:
        """Execute a text-parsed tool call (positional args from raw string)."""
        args = [a.strip().strip("\"'") for a in args_raw.split(",") if a.strip()]
        return await self._run_tool_guarded(name, tuple(args), {})

    async def _execute_tool_call(self, name: str, arguments: dict[str, Any]) -> str:
        """Execute a structured (native) tool call with keyword arguments."""
        return await self._run_tool_guarded(name, (), dict(arguments))

    async def _run_tool_guarded(
        self, name: str, args: tuple[Any, ...], kwargs: dict[str, Any]
    ) -> str:
        tool = self._tools.get(name)
        if tool is None:
            return f"Error: unknown tool '{name}'. Available tools: {list(self._tools)}"

        async def _invoke() -> Any:
            if inspect.iscoroutinefunction(tool.fn):
                coro = tool.fn(*args, **kwargs)
            else:
                coro = asyncio.to_thread(tool.fn, *args, **kwargs)
            if self._tool_timeout is not None:
                return await asyncio.wait_for(coro, timeout=self._tool_timeout)
            return await coro

        for attempt in range(self._tool_retries + 1):
            try:
                result = await _invoke()
                # Cap the observation so a large tool result can't
                # bloat/overflow the context window on the next reasoning turn.
                return truncate_tool_output(str(result))
            except TimeoutError:
                # Also reachable via a tool's own socket timeout (builtin
                # TimeoutError subclasses OSError, so this clause must come
                # first) — hence the None-safe wording.
                after = (
                    f" after {self._tool_timeout:.1f}s"
                    if self._tool_timeout is not None
                    else ""
                )
                logger.warning("Tool '%s' timed out%s", name, after)
                return f"Error executing '{name}': timed out{after}"
            except (ConnectionError, OSError) as exc:
                if attempt < self._tool_retries:
                    delay = self._retry_backoff * (2**attempt)
                    logger.warning(
                        "Tool '%s' transient failure (%s), retry %d/%d in %.1fs",
                        name,
                        type(exc).__name__,
                        attempt + 1,
                        self._tool_retries,
                        delay,
                    )
                    await asyncio.sleep(delay)
                    continue
                logger.warning("Tool '%s' raised %s: %s", name, type(exc).__name__, exc)
                return f"Error executing '{name}': {exc}"
            except Exception as exc:
                logger.warning("Tool '%s' raised %s: %s", name, type(exc).__name__, exc)
                return f"Error executing '{name}': {exc}"
        # Unreachable: every path in the loop returns.
        return f"Error executing '{name}': exhausted retries"

    def _get_llm_service(self):
        if self._llm_service is not None:
            return self._llm_service
        try:
            from core.services.llm import get_llm_service

            return get_llm_service()
        except Exception:
            return None

    # ------------------------------------------------------------------
    # Convenience: format trace for logging / display
    # ------------------------------------------------------------------

    @staticmethod
    def format_trace(result: ReActResult) -> str:
        """Return a human-readable representation of a ReAct trace."""
        lines = [f"=== ReAct Trace ({result.iterations_used} iterations) ==="]
        for step in result.trace:
            lines.append(str(step))
        if result.hit_limit:
            lines.append(
                "[WARNING] Iteration limit reached — answer may be incomplete."
            )
        lines.append(f"\nFinal Answer: {result.final_answer}")
        return "\n".join(lines)


__all__ = [
    "ReActAgent",
    "ReActResult",
    "StepType",
    "ToolDefinition",
    "TraceStep",
]

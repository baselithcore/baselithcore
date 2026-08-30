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
                              description="Search the web",
                              category="read_only")],
        max_iterations=5,
    )
    result = await agent.run("What is the population of Tokyo?")
    print(result.final_answer)
    for step in result.trace:
        print(step)

The trace data structures live in :mod:`core.reasoning.react_types` and the
guarded tool executor in :mod:`core.reasoning.react_tools`; both are re-exported
here, so ``core.reasoning.react`` remains the single public import path.
"""

from __future__ import annotations

import re
from typing import Any

from core.loops.stall import StallGuard
from core.observability.logging import get_logger
from core.reasoning.react_tools import ToolExecutionMixin
from core.reasoning.react_types import (
    ReActResult,
    StepType,
    ToolDefinition,
    TraceStep,
)

logger = get_logger(__name__)


# Embedded fallback for the registry-served ``react_system`` catalog prompt
# (core/prompts/catalog/react_system.md). ``{{ var }}`` placeholders — the
# registry's template syntax — so file and fallback stay byte-comparable.
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
{{ tool_descriptions }}

Rules:
- Always think before acting.
- Use at most {{ max_iterations }} tool calls in total.
- If you cannot find the answer, say so honestly — never fabricate.
- When you have enough information, write "Final Answer:" on its own line.
"""

_FINAL_ANSWER_RE = re.compile(r"Final Answer:\s*(.*)", re.DOTALL | re.IGNORECASE)
_THOUGHT_RE = re.compile(r"Thought:\s*(.*?)(?=\nAction:|\nFinal Answer:|$)", re.DOTALL)
_ACTION_RE = re.compile(r"Action:\s*(\w+)\(([^)]*)\)", re.IGNORECASE)


class ReActAgent(ToolExecutionMixin):
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
        autonomy_policy: Optional ``AutonomyPolicy``. When set, tools whose
            declared ``category`` requires approval at the policy's level go
            through ``enforce_approval`` before execution (fail-closed when
            no ``human_intervention`` channel exists).
        human_intervention: Optional ``core.human.HumanIntervention``-like
            approval channel consulted by the autonomy gate.
        contract_validator: Optional
            ``core.orchestration.contract.ContractValidator``. When set, a
            tool absent from ``allowed_tools`` or listed in ``must_not`` is
            rejected before execution.
        loop_budget: Optional ``core.orchestration.limits.LoopBudget``. Each
            tool invocation is recorded against the per-request tool-call
            cap; exceeding it aborts the run (fail-closed). Falls back to
            the ambient budget from ``budget_context`` when None.
        checkpoint: Optional ``CheckpointManager`` enabling durable
            pause/resume around approvals (``ApprovalPendingError``).
        max_consecutive_tool_failures: Escalate early instead of letting a
            broken tool burn the whole iteration budget: after this many
            consecutive failed tool observations (any tool; a success
            resets the streak) the loop stops with an explanatory final
            answer. ``None`` disables the guard.
        stall_threshold: Futility guard. The streak above counts *how many*
            failures; this counts how many times the *same* failure came
            back (identical ``core.loops`` failure fingerprint). A loop
            that keeps producing new calls with the same broken result is
            alive, busy, billing, and getting nowhere — after this many
            consecutive identical failures it stops and escalates. Must be
            >= 2. ``None`` (default) disables the guard, preserving the
            historical behavior.
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
        autonomy_policy: Any | None = None,
        human_intervention: Any | None = None,
        contract_validator: Any | None = None,
        loop_budget: Any | None = None,
        checkpoint: Any | None = None,
        max_consecutive_tool_failures: int | None = 3,
        stall_threshold: int | None = None,
    ) -> None:
        self._tools: dict[str, ToolDefinition] = {t.name: t for t in (tools or [])}
        self.max_iterations = max_iterations
        self._llm_service = llm_service
        self._system_prompt_extra = system_prompt_extra
        self._tool_timeout = tool_timeout
        self._tool_retries = max(0, tool_retries)
        self._retry_backoff = retry_backoff
        self._native_tools = native_tools
        if autonomy_policy is None:
            from core.orchestration.autonomy import AutonomyPolicy

            # Fail-closed: an agent constructed without a policy runs
            # SUPERVISED (side-effect categories require approval).
            autonomy_policy = AutonomyPolicy()
        self._autonomy_policy = autonomy_policy
        self._human_intervention = human_intervention
        self._contract_validator = contract_validator
        self._loop_budget = loop_budget
        self._checkpoint = checkpoint
        self._max_consecutive_tool_failures = max_consecutive_tool_failures
        self._failure_streak = 0
        self._stall_guard = (
            StallGuard(threshold=stall_threshold)
            if stall_threshold is not None
            else None
        )

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
            # Count every reasoning pass against the per-request LoopBudget:
            # tool calls alone were recorded before, so
            # ``LoopLimits.max_iterations`` never actually bounded the loop.
            # Raises BudgetExceededError (fail-closed) when the cap is hit.
            budget = self._active_budget()
            if budget is not None:
                budget.tick()

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

                escalation = self._note_tool_outcome(observation)
                if escalation is not None:
                    trace.append(
                        TraceStep(StepType.FINAL_ANSWER, iteration, escalation)
                    )
                    return ReActResult(
                        final_answer=escalation,
                        trace=trace,
                        iterations_used=iteration,
                        hit_limit=True,
                    )

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
        from core.prompts.catalog import resolve_catalog_prompt

        tool_descriptions = (
            "\n".join(f"- {t.name}: {t.description}" for t in self._tools.values())
            or "No tools available."
        )

        # Registry-served (versioned, label-resolved, provenance span) with
        # the embedded template as fallback when the registry is unavailable.
        prompt = resolve_catalog_prompt(
            "react_system",
            {
                "tool_descriptions": tool_descriptions,
                "max_iterations": self.max_iterations,
            },
            fallback_template=_SYSTEM_TEMPLATE,
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

        # Deterministic compaction bounds prompt growth on long runs.
        from core.reasoning.history import compact_messages

        messages = compact_messages(messages)
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

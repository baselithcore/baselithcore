"""Guarded tool execution for the ReAct loop.

Holds everything between "the model asked for a tool" and "here is the
observation": the enforcement chokepoint, argument validation, the idempotency
ledger, the timeout and retry policy, and the consecutive-failure circuit
breaker. The pieces that surround the call itself live in
:mod:`core.reasoning.react_tool_gate` (module size cap).

Mixed into :class:`core.reasoning.react.ReActAgent`; not useful standalone.
"""

from __future__ import annotations

import asyncio
import inspect
import time
from typing import Any

from core.observability.logging import get_logger
from core.orchestration.tool_output import escape_untrusted_markers
from core.reasoning.react_tool_gate import (
    build_gate_context,
    claim_ledger_entry,
    dispatch_post_hook,
    gate_denial,
    invalid_arguments_message,
    new_ledger,
    new_run_id,
    note_tool_outcome,
    render_observation,
)
from core.reasoning.react_types import ToolDefinition

logger = get_logger(__name__)

#: Ceiling on tool calls executed concurrently within one multi-tool turn.
#: Models emit a handful per turn; the bound is there so a pathological fan-out
#: cannot open an unbounded number of sockets or thread-pool slots at once.
MAX_PARALLEL_TOOL_CALLS = 8


def _unknown_tool_message(name: str, tools: dict[str, ToolDefinition]) -> str:
    """Runtime narration for a tool the model invented.

    ``name`` is whatever the model wrote, so it is tool-adjacent content being
    interpolated into a string that lands outside the untrusted envelope; its
    markers are neutralised. The registered names are the operator's own and
    need no scrubbing.
    """
    return (
        f"Error: unknown tool '{escape_untrusted_markers(name)}'. "
        f"Available tools: {list(tools)}"
    )


class ToolExecutionMixin:
    """Tool dispatch, gating and failure accounting for :class:`ReActAgent`.

    Expects the host class to provide ``_tools``, ``_tool_timeout``,
    ``_tool_retries``, ``_retry_backoff``, ``_autonomy_policy``,
    ``_human_intervention``, ``_contract_validator``, ``_loop_budget``,
    ``_checkpoint``, ``_max_consecutive_tool_failures``, ``_failure_streak``
    and ``_stall_guard``.

    Three attributes are optional and lazily defaulted here, so a host that
    predates them keeps working: ``_tool_hooks`` (a per-agent
    :class:`~core.orchestration.hooks.ToolHookRegistry`; the process-wide one
    is used when unset), ``_tool_ledger`` (a
    :class:`~core.orchestration.idempotency.ToolLedger`; an in-process one is
    created on first effectful call) and ``_ledger_run_id``.
    """

    _tools: dict[str, ToolDefinition]
    _tool_timeout: float | None
    _tool_retries: int
    _retry_backoff: float
    _autonomy_policy: Any | None
    _human_intervention: Any | None
    _contract_validator: Any | None
    _loop_budget: Any | None
    _checkpoint: Any | None
    _max_consecutive_tool_failures: int | None
    _failure_streak: int
    _stall_guard: Any | None

    async def _execute_tool(self, name: str, args_raw: str) -> str:
        """Execute a text-parsed tool call (positional args from raw string)."""
        args = [a.strip().strip("\"'") for a in args_raw.split(",") if a.strip()]
        return await self._run_tool_guarded(name, tuple(args), {})

    async def _execute_tool_call(self, name: str, arguments: dict[str, Any]) -> str:
        """Execute a structured (native) tool call with keyword arguments.

        The arguments came from the model, so they are schema-checked before
        anything is gated or dispatched.
        """
        return await self._run_tool_guarded(
            name, (), dict(arguments), validate_schema=True
        )

    async def _execute_tool_calls(
        self, calls: list[tuple[str, dict[str, Any]]]
    ) -> list[str]:
        """Run one turn's ``(name, arguments)`` calls, observations in order.

        A native multi-tool turn emits every call before seeing any result, so
        the calls are independent by construction and running them one at a
        time paid the sum of their latencies instead of the slowest.

        The **gates** still run sequentially, ahead of any execution: approval
        and budget refusals are fail-closed and abort the turn, so a tool later
        in the turn must not already have run when an earlier one is denied.
        Only the approved invocations overlap, bounded so a wide fan-out cannot
        swamp the tool backends.
        """
        if not calls:
            return []

        observations: list[str | None] = [None] * len(calls)
        runnable: list[tuple[int, ToolDefinition, dict[str, Any]]] = []

        for index, (name, arguments) in enumerate(calls):
            tool = self._tools.get(name)
            if tool is None:
                observations[index] = _unknown_tool_message(name, self._tools)
                continue
            kwargs = dict(arguments)
            # Reject malformed arguments before the gate: a call the model got
            # wrong must not consume an approval or a budget entry.
            invalid = invalid_arguments_message(tool, kwargs)
            if invalid is not None:
                observations[index] = invalid
                continue
            # Propagates ApprovalPendingError / BudgetExceededError, which must
            # abort the turn before any later tool in it executes.
            denial = await self._enforce_tool_gates(tool, args=kwargs)
            if denial is not None:
                observations[index] = denial
                continue
            runnable.append((index, tool, kwargs))

        if runnable:
            if self._checkpoint is not None:
                # Durable mode runs the turn sequentially: the checkpoint's
                # replay cursor must assign the same key to the same call on
                # every pass, and concurrent per-step saves would interleave
                # version bumps in the store. Correctness of resume beats
                # intra-turn latency here.
                for index, tool, kwargs in runnable:
                    observations[index] = await self._invoke_tool(tool, (), kwargs)
            else:
                gate = asyncio.Semaphore(MAX_PARALLEL_TOOL_CALLS)

                async def _run(tool: ToolDefinition, kwargs: dict[str, Any]) -> str:
                    async with gate:
                        return await self._invoke_tool(tool, (), kwargs)

                results = await asyncio.gather(
                    *(_run(tool, kwargs) for _, tool, kwargs in runnable)
                )
                for (index, _, _), observation in zip(runnable, results, strict=True):
                    observations[index] = observation

        return [o if o is not None else "" for o in observations]

    def _effective_tool_timeout(self) -> float | None:
        """Per-call timeout: the configured cap, shrunk to the ambient
        LoopBudget's remaining wall-clock so one tool can't outlive the
        request deadline. Falls back to the static cap outside an
        orchestrated request."""
        try:
            from core.orchestration.budget_context import get_active_budget

            budget = get_active_budget()
            remaining = budget.remaining_seconds() if budget is not None else None
        except Exception:
            remaining = None
        if remaining is None:
            return self._tool_timeout
        if self._tool_timeout is None:
            return max(remaining, 0.001)
        return max(min(self._tool_timeout, remaining), 0.001)

    def _active_budget(self) -> Any | None:
        """Explicit LoopBudget when injected, else the ambient request budget."""
        if self._loop_budget is not None:
            return self._loop_budget
        try:
            from core.orchestration.budget_context import get_active_budget

            return get_active_budget()
        except Exception:
            return None

    async def _enforce_tool_gates(
        self, tool: ToolDefinition, args: Any | None = None
    ) -> str | None:
        """Push one invocation through the single enforcement chokepoint.

        Delegates to :func:`core.orchestration.enforcement.enforce_tool_invocation`
        rather than re-checking contract / autonomy / budget by hand. That is
        the whole point: every control the chokepoint gains — plugin capability
        checks, the tool rate limit, veto-capable pre-hooks, the audit trail —
        now applies to the ReAct loop too, instead of only to orchestrated
        handlers.

        Args:
            tool: The tool about to run.
            args: Its arguments, hashed (never stored raw) into the audit
                record and the pre-hook event.

        Returns:
            An error-observation string when the call is denied — the loop
            continues and the model can adapt — or None when it may proceed.

        Raises:
            ApprovalPendingError: Durable human-in-the-loop pause.
            BudgetExceededError: A per-request cap was hit (fail-closed).
        """
        from core.orchestration.autonomy import ApprovalPendingError
        from core.orchestration.enforcement import enforce_tool_invocation
        from core.orchestration.limits import BudgetExceededError

        try:
            await enforce_tool_invocation(
                build_gate_context(self),
                tool.name,
                tool.normalized_category(),
                args=args,
            )
        except (ApprovalPendingError, BudgetExceededError):
            # Fail-closed by design: the run pauses durably, or aborts.
            raise
        except Exception as exc:
            return gate_denial(tool.name, exc)
        return None

    # ------------------------------------------------------------------
    # Idempotency ledger (non-read_only categories only)
    # ------------------------------------------------------------------

    def _ledger(self) -> Any:
        """The agent's tool ledger, created in-process on first use."""
        ledger = getattr(self, "_tool_ledger", None)
        if ledger is None:
            ledger = new_ledger()
            self._tool_ledger = ledger
        return ledger

    def _ledger_entries(self) -> int:
        """How many calls this agent's ledger holds (diagnostics / tests)."""
        ledger = getattr(self, "_tool_ledger", None)
        entries = getattr(ledger, "_entries", None)
        return len(entries) if entries is not None else 0

    def _ledger_identity(self) -> tuple[str, int]:
        """``(run_id, step)`` identifying the next effectful call.

        Under a checkpoint the step is the replay cursor, so the same call gets
        the same key on every pass. Without one it is a per-agent counter: keys
        stay stable within an attempt, which is as much as an in-process ledger
        can honestly promise.
        """
        run_id = getattr(self, "_ledger_run_id", None)
        if run_id is None:
            run_id = getattr(self._checkpoint, "run_id", None) or new_run_id()
            self._ledger_run_id = run_id
        cursor = getattr(self._checkpoint, "_cursor", None)
        if isinstance(cursor, int):
            return run_id, cursor
        step = getattr(self, "_ledger_step", 0)
        self._ledger_step = step + 1
        return run_id, step

    def _note_tool_outcome(self, observation: str) -> str | None:
        """Track the consecutive-failure streak; return an escalation message
        when the configured cap is crossed, else None.

        Body in :func:`core.reasoning.react_tool_gate.note_tool_outcome`
        (extracted for the module size cap).
        """
        return note_tool_outcome(self, observation)

    async def _run_tool_guarded(
        self,
        name: str,
        args: tuple[Any, ...],
        kwargs: dict[str, Any],
        *,
        validate_schema: bool = False,
    ) -> str:
        """Look the tool up, validate, gate it, then run it.

        Args:
            name: Tool name as the model spelled it.
            args: Positional arguments (legacy text-parsed loop only).
            kwargs: Keyword arguments.
            validate_schema: Check ``kwargs`` against the tool's JSON Schema
                first. On for structured calls, where the model authored a
                typed object; off for the text loop, whose comma-split strings
                are positional and describe nothing a schema can judge.
        """
        tool = self._tools.get(name)
        if tool is None:
            return _unknown_tool_message(name, self._tools)

        if validate_schema:
            invalid = invalid_arguments_message(tool, kwargs)
            if invalid is not None:
                return invalid

        denial = await self._enforce_tool_gates(tool, args=kwargs or list(args))
        if denial is not None:
            return denial

        return await self._invoke_tool(tool, args, kwargs)

    async def _invoke_tool(
        self, tool: ToolDefinition, args: tuple[Any, ...], kwargs: dict[str, Any]
    ) -> str:
        """Run an already-gated tool, durably when a checkpoint is attached.

        With a :class:`~core.orchestration.checkpoint.CheckpointManager`, the
        invocation goes through ``run_step``: the observation is recorded
        under a deterministic ``(cursor, tool, args)`` key, and a resumed run
        replays the stored observation instead of re-executing the side
        effect. Without a checkpoint, behavior is unchanged.
        """
        if self._checkpoint is not None:
            payload = {"args": list(args), "kwargs": kwargs}
            result = await self._checkpoint.run_step(
                tool.name,
                payload,
                lambda: self._invoke_tool_ledgered(tool, args, kwargs),
                category=tool.category,
            )
            return str(result)
        return await self._invoke_tool_ledgered(tool, args, kwargs)

    async def _invoke_tool_ledgered(
        self, tool: ToolDefinition, args: tuple[Any, ...], kwargs: dict[str, Any]
    ) -> str:
        """Wrap an effectful invocation in an idempotency-ledger claim.

        ``read_only`` tools pass straight through — repeating a read costs a
        round trip, not a defect. Everything else (including an unrecognised
        category, which is treated as effectful) records its intent before the
        call and its outcome after, so a replay returns the recorded result
        instead of sending the payment / email / webhook twice.
        """
        if tool.is_read_only:
            return await self._invoke_tool_uncheckpointed(tool, args, kwargs)
        ledger_args = dict(kwargs) if kwargs else {"__args__": list(args)}
        run_id, step = self._ledger_identity()
        key, replayed = await claim_ledger_entry(
            self._ledger(), run_id, step, tool, ledger_args
        )
        if replayed is not None:
            return replayed
        observation = await self._invoke_tool_uncheckpointed(tool, args, kwargs)
        if key is not None:
            try:
                if observation.startswith("Error"):
                    await self._ledger().fail(key, observation)
                else:
                    await self._ledger().complete(key, observation)
            except Exception as exc:  # the ledger must not break the loop
                logger.warning(
                    "tool_ledger_record_failed tool=%s error=%s", tool.name, exc
                )
        return observation

    async def _invoke_tool_uncheckpointed(
        self, tool: ToolDefinition, args: tuple[Any, ...], kwargs: dict[str, Any]
    ) -> str:
        """Run an already-gated tool, applying timeout, retries and rendering.

        Split from :meth:`_run_tool_guarded` so a multi-tool turn can gate its
        calls in order and then overlap only the invocations.
        """
        name = tool.name
        # This method's error strings are runtime narration and therefore live
        # OUTSIDE the untrusted envelope. ``safe_name`` and the escaped
        # exception text below are what keeps that trusted region trusted.
        safe_name = escape_untrusted_markers(name)
        started = time.perf_counter()

        async def _invoke() -> Any:
            if inspect.iscoroutinefunction(tool.fn):
                coro = tool.fn(*args, **kwargs)
            else:
                coro = asyncio.to_thread(tool.fn, *args, **kwargs)
            timeout = self._effective_tool_timeout()
            # `asyncio.timeout(None)` is a no-op deadline: one path, not two.
            async with asyncio.timeout(timeout):
                return await coro

        async def _finish(observation: str, ok: bool) -> str:
            await dispatch_post_hook(
                self, tool, ok=ok, elapsed_ms=(time.perf_counter() - started) * 1000
            )
            return observation

        for attempt in range(self._tool_retries + 1):
            try:
                result = await _invoke()
                # SkillResult is unpacked (snapshot to the model, success to
                # the failure streak); everything else is bounded, scanned and
                # sealed in the untrusted envelope.
                observation, ok = render_observation(name, result)
                return await _finish(observation, ok)
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
                return await _finish(
                    f"Error executing '{safe_name}': timed out{after}", False
                )
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
                return await _finish(
                    f"Error executing '{safe_name}': "
                    f"{escape_untrusted_markers(str(exc))}",
                    False,
                )
            except Exception as exc:
                logger.warning("Tool '%s' raised %s: %s", name, type(exc).__name__, exc)
                return await _finish(
                    f"Error executing '{safe_name}': "
                    f"{escape_untrusted_markers(str(exc))}",
                    False,
                )
        # Unreachable: every path in the loop returns.
        return f"Error executing '{safe_name}': exhausted retries"


__all__ = ["ToolExecutionMixin"]

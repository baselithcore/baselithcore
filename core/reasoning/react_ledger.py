"""The idempotency-ledger and checkpoint half of ReAct tool execution.

Split from :mod:`core.reasoning.react_tools` (module size cap). Everything here
decides whether a gated call *executes* or is *replayed*: the checkpoint step
(:meth:`~core.orchestration.checkpoint.CheckpointManager.run_step`) around it,
and the ledger claim inside it. Both key the call by its content and its
occurrence in the run (:mod:`core.orchestration.call_keys`), never by its
position, so a resumed run — text loop or native structured loop, which share
this path — replays a completed effect whichever order it reaches it in.

Mixed into :class:`core.reasoning.react.ReActAgent` through
:class:`~core.reasoning.react_tools.ToolExecutionMixin`.
"""

from __future__ import annotations

from typing import Any

from core.observability.logging import get_logger
from core.orchestration.call_keys import CallOccurrences
from core.reasoning.react_tool_gate import (
    claim_ledger_entry,
    new_ledger,
    new_run_id,
)
from core.reasoning.react_types import ToolDefinition

logger = get_logger(__name__)

__all__ = ["ToolLedgerMixin", "observation_is_error"]


def observation_is_error(observation: str) -> bool:
    """Whether an observation describes a failure rather than a result.

    The loop's own narration is the only part of an observation outside the
    untrusted envelope, and it is the part that carries the ``Error`` prefix.
    Tool-controlled text is sealed inside the envelope and has its markers
    escaped, so it cannot forge one — which is what makes reading the prefix
    sound rather than a guess. The ledger already decided success this way;
    naming it keeps the one convention in one place, so the flag the model
    sees on a ``tool_result`` block and the outcome the ledger records cannot
    disagree.

    Args:
        observation: The rendered observation.

    Returns:
        True when the observation is a runtime-authored failure.
    """
    return observation.startswith("Error")


class ToolLedgerMixin:
    """Checkpoint- and ledger-backed invocation of an already-gated tool.

    Expects the host to provide ``_checkpoint`` and
    ``_invoke_tool_uncheckpointed`` (from
    :class:`~core.reasoning.react_tools.ToolExecutionMixin`). ``_tool_ledger``,
    ``_ledger_run_id`` and ``_ledger_occurrences`` are optional and lazily
    defaulted.
    """

    _checkpoint: Any | None

    async def _invoke_tool_uncheckpointed(
        self, tool: ToolDefinition, args: tuple[Any, ...], kwargs: dict[str, Any]
    ) -> str:
        """Run the tool itself; provided by the host mixin."""
        raise NotImplementedError

    # ------------------------------------------------------------------
    # Idempotency ledger (non-read_only categories only)
    # ------------------------------------------------------------------

    #: Sentinel distinguishing "not resolved yet" from "resolved to nothing".
    _LEDGER_UNSET = object()

    def _ledger(self) -> Any:
        """The agent's tool ledger, resolved once on first use.

        ``None`` is a real answer (``ORCHESTRATOR_TOOL_LEDGER=off``), so it is
        cached like any other: testing the ledger itself for ``None`` would
        re-resolve — and re-log — on every effectful call.
        """
        ledger = getattr(self, "_tool_ledger", self._LEDGER_UNSET)
        if ledger is self._LEDGER_UNSET or (
            ledger is None and not getattr(self, "_tool_ledger_resolved", False)
        ):
            ledger = new_ledger()
            self._tool_ledger = ledger
            self._tool_ledger_resolved = True
        return ledger

    def _ledger_entries(self) -> int:
        """How many calls this agent's ledger holds (diagnostics / tests)."""
        ledger = getattr(self, "_tool_ledger", None)
        entries = getattr(ledger, "_entries", None)
        return len(entries) if entries is not None else 0

    def _ledger_occurrence(self, tool_name: str, args: Any) -> int:
        """Occurrence of ``(tool_name, args)`` for a call outside a checkpoint.

        Per agent, like ``_ledger_run_id``: a run resumed without a checkpoint
        is a fresh agent given the same run id, so it counts from zero again
        and lands on the keys the crashed attempt recorded.
        """
        tracker = getattr(self, "_ledger_occurrences", None)
        if tracker is None:
            tracker = CallOccurrences()
            self._ledger_occurrences = tracker
        return tracker.next(tool_name, args)

    def _ledger_tenant(self) -> str:
        """Tenant mixed into ledger keys: the checkpoint's, else the ambient one.

        The checkpoint's wins because a crash-recovery sweep may resume the run
        with no tenant bound, and a key that changed with the ambient context
        would miss every row the original pass wrote.
        """
        recorded = getattr(
            getattr(self._checkpoint, "checkpoint", None), "tenant_id", None
        )
        if isinstance(recorded, str) and recorded:
            return recorded
        from core.context import get_tenant_or_default

        return get_tenant_or_default()

    def _ledger_identity(self) -> tuple[str, int]:
        """``(run_id, step)`` for the next effectful call.

        ``step`` only feeds the legacy positional key
        (:func:`~core.orchestration.idempotency.claim_call`): under a
        checkpoint it is the replay cursor, otherwise a per-agent counter. The
        live key is content-addressed and ignores it.
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

    async def _invoke_tool(
        self, tool: ToolDefinition, args: tuple[Any, ...], kwargs: dict[str, Any]
    ) -> str:
        """Run an already-gated tool, durably when a checkpoint is attached.

        With a :class:`~core.orchestration.checkpoint.CheckpointManager`, the
        invocation goes through ``run_step``: the observation is recorded
        under a content-addressed ``(tool, args, occurrence)`` key, and a
        resumed run replays the stored observation instead of re-executing the
        side effect — whichever order it reaches it in. The ledger key inside
        the step draws on the same occurrence, so the two layers cannot
        disagree about which call this is. Without a checkpoint, behavior is
        unchanged.
        """
        if self._checkpoint is not None:
            payload = {"args": list(args), "kwargs": kwargs}
            step_options: dict[str, Any] = {"category": tool.category}
            occurrence: int | None = None
            next_occurrence = getattr(self._checkpoint, "next_occurrence", None)
            if callable(next_occurrence):
                occurrence = next_occurrence(tool.name, payload)
                step_options["occurrence"] = occurrence
            result = await self._checkpoint.run_step(
                tool.name,
                payload,
                lambda: self._invoke_tool_ledgered(
                    tool, args, kwargs, occurrence=occurrence
                ),
                **step_options,
            )
            return str(result)
        return await self._invoke_tool_ledgered(tool, args, kwargs)

    async def _invoke_tool_ledgered(
        self,
        tool: ToolDefinition,
        args: tuple[Any, ...],
        kwargs: dict[str, Any],
        *,
        occurrence: int | None = None,
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
        if occurrence is None:
            occurrence = self._ledger_occurrence(tool.name, ledger_args)
        key, replayed = await claim_ledger_entry(
            self._ledger(),
            run_id,
            step,
            tool,
            ledger_args,
            occurrence=occurrence,
            tenant_id=self._ledger_tenant(),
        )
        if replayed is not None:
            return replayed
        observation = await self._invoke_tool_uncheckpointed(tool, args, kwargs)
        if key is not None:
            try:
                if observation_is_error(observation):
                    await self._ledger().fail(key, observation)
                else:
                    await self._ledger().complete(key, observation)
            except Exception as exc:  # the ledger must not break the loop
                logger.warning(
                    "tool_ledger_record_failed tool=%s error=%s", tool.name, exc
                )
        return observation

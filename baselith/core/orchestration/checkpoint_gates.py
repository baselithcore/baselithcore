"""The approval-gate half of :class:`CheckpointManager`.

Split out of ``checkpoint`` (module size cap). A gate is evaluated by
``approval_decision`` and, when nobody has decided it, paused by
``await_approval``; both are bound to the call's digest and to the visit — the
scope and the pass's occurrence — described in
:mod:`baselith.core.orchestration.checkpoint_approval_match`.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from baselith.core.orchestration.checkpoint_approval_match import (
    GateVisits,
    decision_for,
    new_pending_request,
)

if TYPE_CHECKING:
    from baselith.core.orchestration.checkpoint import Checkpoint

__all__ = ["ApprovalGateMixin"]


class ApprovalGateMixin:
    """Durable approval gates over ``self.checkpoint`` (the host supplies it)."""

    checkpoint: Checkpoint

    @property
    def _gates(self) -> GateVisits:
        """Visit counter of the current pass (built on first use)."""
        gates: GateVisits | None = self.__dict__.get("_gate_visits")
        if gates is None:
            gates = self.__dict__["_gate_visits"] = GateVisits()
        return gates

    async def await_approval(
        self,
        tool_name: str,
        category: str,
        args_digest: str | None = None,
        *,
        scope: str = "",
        preview: str | None = None,
    ) -> None:
        """Pause the run pending human approval and persist that state.

        The request is bound to one call (``args_digest``) and one visit
        (``scope`` and the pass's occurrence); ``preview`` is what the reviewer
        reads. Resume once a decision is recorded via
        :func:`record_approval_decision`.
        """
        from baselith.core.orchestration.checkpoint import STATUS_AWAITING_APPROVAL

        self.checkpoint.status = STATUS_AWAITING_APPROVAL
        self.checkpoint.pending_approval = new_pending_request(
            tool_name,
            category,
            args_digest,
            self.checkpoint.pending_approval,
            scope=scope,
            occurrence=self._gates.claim((scope, tool_name, category, args_digest)),
            preview=preview,
        )
        self._sync_budget()  # type: ignore[attr-defined]
        await self._persist()  # type: ignore[attr-defined]

    def approval_decision(
        self,
        tool_name: str,
        category: str,
        args_digest: str | None = None,
        *,
        scope: str = "",
    ) -> bool | None:
        """Recorded reviewer decision for this visit, if any.

        True (approved) / False (denied) when a decision for this call, scope
        and occurrence exists; None for a fresh gate. Each call is one visit.
        """
        occurrence = self._gates.visit((scope, tool_name, category, args_digest))
        return decision_for(
            self.checkpoint.pending_approval,
            tool_name,
            category,
            args_digest,
            scope=scope,
            occurrence=occurrence,
        )

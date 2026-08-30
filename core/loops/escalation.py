"""Default escalation sink for :class:`~core.loops.engineered.EngineeredLoop`.

The loop's ``escalate`` hook is where a losing campaign hands a resumable
state to a human — but a hook that defaults to ``None`` means most loops
lose silently. This module composes the two channels the framework already
ships — :class:`~core.human.interaction.HumanIntervention` notifications and
webhook emission (event ``loop.escalated``) — into one best-effort hook:
every configured sink is attempted, a failing sink is logged and skipped,
and the loop's own finish path is never broken by its escalation.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from core.loops.engineered import EscalationHook, LoopOutcome
from core.observability.logging import get_logger

if TYPE_CHECKING:
    from core.human.interaction import HumanIntervention
    from core.webhooks.service import WebhookService

logger = get_logger(__name__)

#: Webhook event type emitted for a non-success loop outcome.
LOOP_ESCALATED_EVENT = "loop.escalated"


def build_default_escalation(
    human: HumanIntervention | None = None,
    webhooks: WebhookService | None = None,
    *,
    tenant_id: str = "default",
) -> EscalationHook:
    """Compose an escalation hook from the configured channels.

    Args:
        human: Notification channel; receives a one-line summary plus the
            resumable ``LoopOutcome.to_state()`` payload as context.
        webhooks: Webhook service; subscribers to ``loop.escalated`` get the
            same payload.
        tenant_id: Tenant scope for the webhook emission.

    Returns:
        An async hook suitable for ``EngineeredLoop(escalate=...)``. With no
        channels configured it degrades to a logged no-op — still better
        than ``None``, because the loss is at least visible in the logs.
    """

    async def escalate(outcome: LoopOutcome) -> None:
        state: dict[str, Any] = outcome.to_state()
        message = (
            f"Engineered loop {outcome.status} after {outcome.attempts} "
            f"attempt(s): {outcome.goal}"
        )
        if human is not None:
            try:
                await human.notify(message, context=state)
            except Exception as exc:
                logger.warning("loop_escalation_notify_failed error=%s", exc)
        if webhooks is not None:
            try:
                await webhooks.emit(LOOP_ESCALATED_EVENT, state, tenant_id=tenant_id)
            except Exception as exc:
                logger.warning("loop_escalation_webhook_failed error=%s", exc)
        if human is None and webhooks is None:
            logger.warning("loop_escalated_no_sink status=%s", outcome.status)

    return escalate


__all__ = ["LOOP_ESCALATED_EVENT", "build_default_escalation"]

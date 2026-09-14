"""Operator-side approval decisions for runs paused ``awaiting_approval``.

The durable human-in-the-loop flow has two halves. The agent side lives in
:mod:`core.orchestration.checkpoint` (``CheckpointManager.await_approval`` /
``approval_decision``); this is the operator side: recording *who* decided,
*how they were authenticated*, and leaving an audit trail behind.

An approval is the moment a human takes responsibility for a side effect the
policy refused to let the agent take alone. A decision attributed to a free
string ("gio"), with no record of how that identity was established and no
audit event, cannot answer the only questions that matter afterwards — who
approved it, and were they really who they claimed. Hence
:class:`ApprovalPrincipal` and the ``approval.decision`` audit event.

Split from ``checkpoint.py`` for the module size cap;
:func:`record_approval_decision` is re-exported there, which remains the
public import path.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from core.observability.logging import get_logger

if TYPE_CHECKING:
    from core.orchestration.checkpoint import CheckpointStore

logger = get_logger(__name__)

__all__ = ["PROGRAMMATIC", "ApprovalPrincipal", "record_approval_decision"]

#: Auth method recorded when a programmatic caller supplied only a name (a
#: workflow engine, a test, a CLI running as the operator). It is not a claim
#: that the reviewer was unauthenticated — only that *this* call carried no
#: attestation of how they were, which is exactly what an auditor needs to
#: distinguish from a verified identity. An HTTP surface must never use it:
#: there, the identity comes from the authenticated request.
PROGRAMMATIC = "programmatic"


@dataclass(frozen=True)
class ApprovalPrincipal:
    """Who approved (or denied) a paused run, and how they authenticated.

    Attributes:
        id: Stable identifier of the reviewer (user id, service account,
            operator handle). Never blank.
        auth_method: How that identity was established — e.g. ``oidc``,
            ``mtls``, ``api_key``, ``http_basic_admin``. ``programmatic`` when
            an in-process caller supplied a bare name with no attestation.
    """

    id: str
    auth_method: str = PROGRAMMATIC

    def __post_init__(self) -> None:
        if not self.id or not self.id.strip():
            raise ValueError("ApprovalPrincipal.id must be a non-empty identifier")
        if not self.auth_method or not self.auth_method.strip():
            raise ValueError("ApprovalPrincipal.auth_method must be non-empty")

    def to_dict(self) -> dict[str, str]:
        """Serialize to the shape persisted on the checkpoint."""
        return {"id": self.id, "auth_method": self.auth_method}

    @classmethod
    def from_value(cls, value: ApprovalPrincipal | str | None) -> ApprovalPrincipal:
        """Coerce an ``approver`` argument into a principal.

        A principal passes through. A bare string becomes a ``programmatic``
        principal, so in-process callers (a workflow engine, a CLI running as
        the operator) keep working and the audit trail says plainly how much
        the identity was worth.

        ``None`` **raises**. An approval is the moment a human takes
        responsibility for something the policy refused to let the agent do
        alone; recording one against nobody is worse than not recording it,
        because it looks like an answer to "who approved this?" while being
        the absence of one.

        Args:
            value: A principal, a bare reviewer name, or ``None``.

        Returns:
            The resolved :class:`ApprovalPrincipal`.

        Raises:
            ValueError: ``value`` names no reviewer at all.
        """
        if isinstance(value, cls):
            return value
        if isinstance(value, str) and value.strip():
            return cls(id=value.strip(), auth_method=PROGRAMMATIC)
        raise ValueError(
            "an approval decision requires an approver: pass an "
            "ApprovalPrincipal built from the authenticated identity, or a "
            "reviewer id for a programmatic caller"
        )


async def _emit_decision_audit(
    run_id: str,
    tenant_id: str | None,
    approved: bool,
    principal: ApprovalPrincipal,
    pending: dict[str, Any],
    reason: str | None,
    label: str | None,
) -> None:
    """Record the decision on the audit trail; never fails the decision."""
    try:
        from core.observability.audit import AuditEventType, get_audit_logger

        details: dict[str, Any] = {
            "approver_id": principal.id,
            "auth_method": principal.auth_method,
            "approved": approved,
            "tool_name": pending.get("tool_name"),
            "category": pending.get("category"),
        }
        if reason:
            details["reason"] = reason
        if label:
            # A display name the requester chose. Recorded next to — never
            # instead of — the authenticated id, so it can never be mistaken
            # for the identity.
            details["approver_label"] = label
        await get_audit_logger().log(
            AuditEventType.APPROVAL_DECISION,
            user_id=principal.id,
            resource=run_id,
            action="approve" if approved else "deny",
            tenant_id=tenant_id,
            details=details,
            success=True,
        )
    except Exception:  # pragma: no cover - observability must not lose a decision
        logger.warning("approval_decision_audit_failed run=%s", run_id, exc_info=True)


async def record_approval_decision(
    store: CheckpointStore,
    run_id: str,
    approved: bool,
    *,
    approver: ApprovalPrincipal | str | None = None,
    reason: str | None = None,
    approver_label: str | None = None,
) -> bool:
    """Record a reviewer's decision on a run paused ``awaiting_approval``.

    The operator-facing half of the durable human-in-the-loop flow: persist
    the decision, then re-run ``process(run_id=..., resume=True)`` — the
    approval gate consumes the decision and the run continues (approved) or
    aborts with a denial (denied).

    Args:
        store: The shared checkpoint store.
        run_id: The paused run.
        approved: The reviewer's verdict.
        approver: The deciding principal. An HTTP surface must build an
            :class:`ApprovalPrincipal` from the *authenticated* identity, so
            the audit trail records how the reviewer was authenticated; a bare
            string is accepted for in-process callers and recorded as
            ``programmatic``.
        reason: Optional free-text justification, recorded and audited.
        approver_label: Optional display name the requester supplied. Recorded
            beside the principal, never as it — a client-chosen string is not
            an identity.

    Returns:
        True when the decision was recorded; False when the run is unknown or
        has no pending approval.

    Raises:
        ValueError: ``approver`` names no reviewer (see
            :meth:`ApprovalPrincipal.from_value`).
    """
    # Resolve the principal BEFORE touching the store: an unattributable
    # decision must fail loudly, not half-write.
    principal = ApprovalPrincipal.from_value(approver)
    checkpoint = await store.load(run_id)
    if checkpoint is None or not checkpoint.pending_approval:
        return False
    checkpoint.pending_approval["decision"] = {
        "approved": approved,
        # ``approver`` stays the plain id: the persisted shape predates the
        # principal and is read by the approvals API and stored checkpoints.
        "approver": principal.id,
        "approver_auth_method": principal.auth_method,
        "approver_label": approver_label,
        "reason": reason,
        "at": time.time(),
    }
    await store.save(checkpoint)
    await _emit_decision_audit(
        run_id,
        checkpoint.tenant_id,
        approved,
        principal,
        checkpoint.pending_approval,
        reason,
        approver_label,
    )
    return True

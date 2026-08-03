"""Periodic review sweep across the governance artefacts.

Three obligations in this subsystem are **recurring**, not one-off, and each one
fails the same way: the artefact is produced once, nobody revisits it, and it
quietly stops describing the system it documents.

* **Art. 9(1)** — the risk management system is a continuous iterative process,
  "regularly systematically reviewed and updated";
* **Art. 72(1)** — the post-market monitoring system must stay *active*;
* **GDPR Art. 35(11)** — the controller reviews the DPIA where the risk changes.

A per-artefact `overdue_reviews()` that nobody polls only moves the failure one
step: the information exists, unread. This scheduler is what turns those
accessors into a signal — one daily pass, one audit record, one warning line per
overdue artefact naming the article behind it.

It also surfaces the DPIAs whose processing may not lawfully start (Art. 36(1)
prior consultation outstanding), because that is the one state in this file that
is not merely untidy but unlawful.

The sweep reports. Acting on it — reviewing the file, consulting the authority,
pausing the processing — stays with the operator.
"""

from __future__ import annotations

import asyncio
from typing import Any

from core.observability.audit import AuditEventType, get_audit_logger
from core.observability.logging import get_logger

logger = get_logger(__name__)

# A daily pass bounds how long an overdue review goes unreported to ~24h, at
# negligible cost.
_SWEEP_INTERVAL_SECONDS = 24 * 3600


class ComplianceReviewScheduler:
    """Owns the periodic governance review sweep and its lifecycle."""

    def __init__(self, interval_seconds: int = _SWEEP_INTERVAL_SECONDS) -> None:
        self._interval = interval_seconds
        self._task: asyncio.Task[None] | None = None

    def start(self) -> None:
        """Schedule the sweep loop. Idempotent — a second call is a no-op."""
        if self._task is not None:
            return
        self._task = asyncio.create_task(self._run(), name="compliance-review-sweep")
        logger.info(
            "compliance_review_scheduler_started",
            extra={"interval_seconds": self._interval},
        )

    async def stop(self) -> None:
        """Cancel the sweep loop and await its teardown. Idempotent."""
        if self._task is None:
            return
        self._task.cancel()
        try:
            await self._task
        except asyncio.CancelledError:
            pass
        self._task = None

    async def _run(self) -> None:
        while True:
            try:
                await self.sweep()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.error("compliance_review_sweep_failed", extra={"error": str(exc)})
            await asyncio.sleep(self._interval)

    async def sweep(self) -> dict[str, list[str]]:
        """Run one pass over every reviewable artefact. Never raises.

        Each subsystem is swept independently: a failure in one (a missing
        store, a broken record) must not hide the overdue artefacts in the
        others.
        """
        findings: dict[str, list[str]] = {
            "overdue_post_market_reviews": [],
            "incomplete_post_market_plans": [],
            "overdue_risk_reviews": [],
            "incomplete_risk_files": [],
            "blocked_dpias": [],
            "incomplete_dpias": [],
        }

        await self._sweep_post_market(findings)
        await self._sweep_risk_management(findings)
        await self._sweep_dpia(findings)

        flagged = {k: v for k, v in findings.items() if v}
        if flagged:
            await get_audit_logger().log(
                AuditEventType.COMPLIANCE_ASSESSMENT,
                action="compliance_review_sweep",
                success=not (
                    findings["overdue_post_market_reviews"]
                    or findings["overdue_risk_reviews"]
                    or findings["blocked_dpias"]
                ),
                details=flagged,
            )
        return findings

    async def _sweep_post_market(self, findings: dict[str, list[str]]) -> None:
        try:
            from core.compliance.post_market_service import get_post_market_service

            service = get_post_market_service()
            for plan in await service.overdue_reviews():
                findings["overdue_post_market_reviews"].append(plan.id)
                logger.warning(
                    "AUDIT | COMPLIANCE | post-market review overdue | system=%s "
                    "plan=%s (Art. 72(1) requires the monitoring system to stay "
                    "active)",
                    plan.system_id,
                    plan.id,
                )
            findings["incomplete_post_market_plans"] = [
                p.id for p in await service.incomplete()
            ]
        except Exception as exc:
            logger.error("post_market_sweep_failed", extra={"error": str(exc)})

    async def _sweep_risk_management(self, findings: dict[str, list[str]]) -> None:
        try:
            from core.compliance.artefact_services import get_risk_management_service

            service = get_risk_management_service()
            for file in await service.overdue_reviews():
                findings["overdue_risk_reviews"].append(file.id)
                logger.warning(
                    "AUDIT | COMPLIANCE | risk management review overdue | "
                    "system=%s file=%s (Art. 9(1) requires a continuous "
                    "iterative process, systematically reviewed)",
                    file.system_id,
                    file.id,
                )
            findings["incomplete_risk_files"] = [
                f.id for f in await service.incomplete()
            ]
        except Exception as exc:
            logger.error("risk_management_sweep_failed", extra={"error": str(exc)})

    async def _sweep_dpia(self, findings: dict[str, list[str]]) -> None:
        try:
            from core.compliance.artefact_services import get_dpia_service

            service = get_dpia_service()
            for assessment in await service.blocked():
                findings["blocked_dpias"].append(assessment.id)
                if assessment.requires_prior_consultation:
                    logger.warning(
                        "AUDIT | COMPLIANCE | DPIA awaiting prior consultation | "
                        "dpia=%s (Art. 36(1): processing must not start until the "
                        "supervisory authority has been consulted)",
                        assessment.id,
                    )
            findings["incomplete_dpias"] = [
                a.id for a in await service.incomplete()
            ]
        except Exception as exc:
            logger.error("dpia_sweep_failed", extra={"error": str(exc)})


def sweep_summary(findings: dict[str, list[str]]) -> dict[str, Any]:
    """Collapse a sweep result to counts plus an overall attention flag."""
    urgent = (
        findings.get("overdue_post_market_reviews", [])
        + findings.get("overdue_risk_reviews", [])
        + findings.get("blocked_dpias", [])
    )
    return {
        "counts": {key: len(value) for key, value in findings.items()},
        "needs_attention": bool(urgent),
    }


__all__ = ["ComplianceReviewScheduler", "sweep_summary"]

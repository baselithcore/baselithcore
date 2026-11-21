"""The auditor's package: scope, criteria matrix, controls and an evidence index.

What an auditor starts from is a request list: *for each criterion, which
controls address it, and show me the evidence*. This builds that view from the
service, adds the audit trail's current head hash (so the package says which
state of the tamper-evident trail it was drawn from), and hashes itself: the
export is recorded on the trail with that digest, so which package was handed
over, and when, is itself evidence.

Evidence *content* is not inlined: the index carries each piece's digest and
where it lives (the structured ``details`` of automatic evidence stay in the
store). The package is a map to the evidence, not a second copy of it.

**The digests are checked, not copied.** Building a package recomputes the
digest of every automatic record from its stored ``details`` and looks up the
digest the audit trail recorded when the evidence was written. Each index
entry says how both went, and ``evidence_integrity`` totals them: a record
edited in the store, or swapped for another with a fresh digest, shows up here
instead of travelling to the auditor as if nothing had happened. An anchor
reads ``missing`` once retention has purged that part of the trail, so keep
``AUDIT_RETENTION_DAYS`` at least as long as the period under examination.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import Any

from baselith.core.compliance.soc2.service import Soc2Service, canonical_sha256
from baselith.core.compliance.soc2.types import EvidenceKind, Soc2Evidence
from baselith.core.observability.audit import AuditEventType, get_audit_logger
from baselith.core.observability.logging import get_logger

logger = get_logger(__name__)

_TRAIL_PAGE = 5000

#: The line every package carries. It is accurate and it is the whole point.
DISCLAIMER = (
    "This is a readiness package assembled from records. It is not a SOC 2 "
    "report and does not state that any control is effective: that is an "
    "independent auditor's opinion."
)


async def _trail_anchor() -> dict[str, Any]:
    try:
        from baselith.core.observability.audit_setup import get_durable_audit_sink

        sink = get_durable_audit_sink()
        if sink is None:
            return {"durable": False}
        head, records = await asyncio.to_thread(
            lambda: (sink.head_hash(), sink.count())
        )
        return {"durable": True, "head_hash": head, "records": records}
    except Exception as exc:
        return {"durable": False, "error": type(exc).__name__}


async def _anchored_digests() -> dict[str, str] | None:
    """Evidence id → the digest the trail recorded for it; ``None`` without a trail.

    The first record wins: evidence is written once, so a later trail entry
    under the same id is not the original.
    """
    try:
        from baselith.core.observability.audit_chain import AuditQuery
        from baselith.core.observability.audit_setup import get_durable_audit_sink

        sink = get_durable_audit_sink()
        if sink is None:
            return None

        def _read() -> dict[str, str]:
            found: dict[str, str] = {}
            cursor = 0
            while True:
                rows = sink.query(
                    AuditQuery(
                        event_type=AuditEventType.COMPLIANCE_ASSESSMENT.value,
                        after_seq=cursor,
                        ascending=True,
                        limit=_TRAIL_PAGE,
                    )
                )
                for row in rows:
                    if row["action"] == "soc2_evidence" and row["resource"]:
                        digest = str(row["details"].get("sha256", ""))
                        found.setdefault(str(row["resource"]), digest)
                if len(rows) < _TRAIL_PAGE:
                    return found
                cursor = int(rows[-1]["seq"])

        return await asyncio.to_thread(_read)
    except Exception as exc:
        # The package is still built; every anchor then reads ``no_trail``.
        logger.warning("soc2_trail_anchors_unreadable", error=type(exc).__name__)
        return None


def _integrity(e: Soc2Evidence, anchored: dict[str, str] | None) -> dict[str, str]:
    """How one record stands against its own digest and against the trail."""
    if e.kind is EvidenceKind.AUTOMATIC:
        digest = "ok" if canonical_sha256(e.details) == e.sha256 else "mismatch"
    else:
        # A manual digest is of a document held elsewhere: not recomputable here.
        digest = "not_recomputable"
    if anchored is None:
        anchor = "no_trail"
    elif e.id not in anchored:
        anchor = "missing"
    else:
        anchor = "matched" if anchored[e.id] == e.sha256 else "mismatch"
    return {"digest": digest, "anchor": anchor}


async def build_package(
    service: Soc2Service, scope_id: str, *, now: datetime | None = None
) -> dict[str, Any]:
    """Assemble the package for ``scope_id`` and record the export."""
    moment = now or datetime.now(UTC)
    # One read of each table: the readiness view and the index below are drawn
    # from the same records, not from two loads a write could land between.
    all_controls = await service.list_controls()
    evidence = await service.list_evidence()
    scope, criteria, controls, summary = await service.assess(
        await service.require_scope(scope_id), all_controls, evidence, moment
    )
    anchored = await _anchored_digests()
    by_control: dict[str, list[dict[str, Any]]] = {}
    integrity: dict[str, Any] = {
        "checked": len(evidence),
        "digest_mismatch": [],
        "anchor_mismatch": [],
        "anchor_missing": 0,
        "trail": "durable" if anchored is not None else "none",
    }
    for e in evidence:
        state = _integrity(e, anchored)
        if state["digest"] == "mismatch":
            integrity["digest_mismatch"].append(e.id)
        if state["anchor"] == "mismatch":
            integrity["anchor_mismatch"].append(e.id)
        integrity["anchor_missing"] += state["anchor"] == "missing"
        by_control.setdefault(e.control_id, []).append(
            {
                "id": e.id,
                "collected_at": e.collected_at.isoformat(),
                "title": e.title,
                "kind": e.kind.value,
                "source": e.source,
                "outcome": e.outcome.value,
                "sha256": e.sha256,
                "reference": e.reference,
                "recorded_by": e.recorded_by,
                "integrity": state,
            }
        )
    integrity["ok"] = not (integrity["digest_mismatch"] or integrity["anchor_mismatch"])
    status_of = {c.control_id: c for c in controls}
    package: dict[str, Any] = {
        "generated_at": moment.isoformat(),
        "disclaimer": DISCLAIMER,
        "scope": scope.to_dict(),
        "summary": summary,
        "criteria": [c.to_dict() for c in criteria],
        "controls": [
            {
                **c.to_dict(),
                "readiness": status_of[c.id].to_dict() if c.id in status_of else None,
                "evidence": by_control.get(c.id, []),
            }
            for c in all_controls
            if c.active
        ],
        "evidence_integrity": integrity,
        "audit_trail": await _trail_anchor(),
    }
    package["package_sha256"] = canonical_sha256(package)
    await get_audit_logger().log(
        AuditEventType.COMPLIANCE_ASSESSMENT,
        resource=scope.id,
        action="soc2_package",
        details={
            "regime": "soc2",
            "package_sha256": package["package_sha256"],
            "gaps": summary["gaps"],
            "evidence_integrity_ok": integrity["ok"],
        },
        success=integrity["ok"],
    )
    return package


def render_markdown(package: dict[str, Any]) -> str:
    """The package as a Markdown request list an auditor can read top to bottom."""
    scope = package["scope"]
    summary = package["summary"]
    lines = [
        f"# SOC 2 readiness package — {scope['name']}",
        "",
        f"> {package['disclaimer']}",
        "",
        f"- Engagement: **{scope['attestation_type']}**, categories: "
        f"{', '.join(scope['categories'])}",
        f"- Period: {scope['period_start']} → {scope['period_end'] or 'open'}",
        f"- Generated: {package['generated_at']}",
        f"- Criteria in scope: {summary['criteria']}, open gaps: {summary['gaps']}",
        f"- Package digest (sha256): `{package['package_sha256']}`",
    ]
    trail = package["audit_trail"]
    if trail.get("durable"):
        lines.append(
            f"- Audit trail head: `{trail['head_hash']}` ({trail['records']} records)"
        )
    integrity = package.get("evidence_integrity")
    if integrity:
        failed = len(integrity["digest_mismatch"]) + len(integrity["anchor_mismatch"])
        lines.append(
            f"- Evidence integrity: {integrity['checked']} checked, "
            f"**{failed} failed**, {integrity['anchor_missing']} with no trail "
            f"anchor (trail: {integrity['trail']})"
        )
    if scope["system_description"]:
        lines += ["", "## System description", "", scope["system_description"]]
    lines += [
        "",
        "## Criteria",
        "",
        "| Criterion | Title | Status | Controls |",
        "|---|---|---|---|",
    ]
    for c in package["criteria"]:
        lines.append(
            f"| {c['id']} | {c['title']} | {c['status']} | {len(c['control_ids'])} |"
        )
    lines += ["", "## Controls and evidence"]
    for control in package["controls"]:
        readiness = control["readiness"] or {}
        lines += [
            "",
            f"### {control['title']}",
            "",
            f"Owner: {control['owner'] or '—'} · Frequency: {control['frequency']} · "
            f"Criteria: {', '.join(control['criteria']) or '—'} · "
            f"Status: {readiness.get('status', '—')}",
        ]
        if control["description"]:
            lines += ["", control["description"]]
        if control["evidence"]:
            lines += [
                "",
                "| Collected | Evidence | Kind | Outcome | sha256 |",
                "|---|---|---|---|---|",
            ]
            for e in control["evidence"]:
                lines.append(
                    f"| {e['collected_at']} | {e['title']} | {e['kind']} | "
                    f"{e['outcome']} | `{e['sha256'][:16]}…` |"
                )
        else:
            lines += ["", "_No evidence recorded._"]
    return "\n".join(lines) + "\n"


__all__ = ["DISCLAIMER", "build_package", "render_markdown"]

"""The auditor's package: scope, criteria matrix, controls and an evidence index.

What an auditor starts from is a request list: *for each criterion, which
controls address it, and show me the evidence*. This builds that view from the
service, adds the audit trail's current head hash (so the package says which
state of the tamper-evident trail it was drawn from), and hashes itself: the
export is recorded on the trail with that digest, so which package was handed
over, and when, is itself evidence.

Evidence *content* is not inlined: the index carries each piece's digest and
where it lives, and automatic evidence carries its structured ``details``. The
package is a map to the evidence, not a second copy of it.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from baselith.core.compliance.soc2.service import Soc2Service, canonical_sha256
from baselith.core.observability.audit import AuditEventType, get_audit_logger

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
        return {"durable": True, "head_hash": sink.head_hash(), "records": sink.count()}
    except Exception as exc:
        return {"durable": False, "error": type(exc).__name__}


async def build_package(
    service: Soc2Service, scope_id: str, *, now: datetime | None = None
) -> dict[str, Any]:
    """Assemble the package for ``scope_id`` and record the export."""
    moment = now or datetime.now(UTC)
    scope, criteria, controls, summary = await service.readiness(scope_id, moment)
    evidence = await service.list_evidence()
    by_control: dict[str, list[dict[str, Any]]] = {}
    for e in evidence:
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
            }
        )
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
            for c in await service.list_controls()
            if c.active
        ],
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
        },
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

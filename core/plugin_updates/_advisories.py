"""Parsing of GitHub Security Advisory API entries into :class:`Advisory`."""

from __future__ import annotations

from typing import Any

from .models import Advisory

SEVERITY_ORDER: dict[str, int] = {
    "critical": 4,
    "high": 3,
    "medium": 2,
    "low": 1,
    "unknown": 0,
}


def _text(value: Any) -> str | None:
    return value if isinstance(value, str) else None


def parse_advisory(entry: Any) -> list[Advisory]:
    """Map one advisory object to one :class:`Advisory` per vulnerability.

    A malformed entry (not an object, no string ``ghsa_id``, no vulnerability
    list) yields an empty list so one bad record never fails the whole check.
    """
    if not isinstance(entry, dict):
        return []
    ghsa = _text(entry.get("ghsa_id"))
    vulns = entry.get("vulnerabilities")
    if not ghsa or not isinstance(vulns, list):
        return []
    severity = (_text(entry.get("severity")) or "").lower()
    if severity not in SEVERITY_ORDER:
        severity = "unknown"
    found: list[Advisory] = []
    for vuln in vulns:
        if not isinstance(vuln, dict):
            continue
        rng = _text(vuln.get("vulnerable_version_range"))
        if rng is None:
            continue
        found.append(
            Advisory(
                ghsa_id=ghsa,
                severity=severity,
                summary=_text(entry.get("summary")) or "",
                html_url=_text(entry.get("html_url")) or "",
                vulnerable_range=rng,
                patched_versions=_text(vuln.get("patched_versions")) or "",
            )
        )
    return found


__all__ = ["SEVERITY_ORDER", "parse_advisory"]

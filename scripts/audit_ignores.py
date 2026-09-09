#!/usr/bin/env python3
"""Turn the accepted-risk register into ``pip-audit --ignore-vuln`` flags.

``.trivyignore.yaml`` is the one place where an unfixable CVE is argued, and
every entry there carries an ``expired_at`` so the finding resurfaces instead of
decaying into a permanent blind spot. Trivy honours that date; ``pip-audit``
takes ids on the command line and knows nothing about it, so a hand-written
``--ignore-vuln`` would outlive the argument that justified it — the blind spot
the register exists to prevent.

This prints the flags for the entries that are still in date, and nothing for
the ones that are not, so an expired exception simply fails the audit again.

    pip-audit --skip-editable $(python scripts/audit_ignores.py)
"""

from __future__ import annotations

import re
import sys
from datetime import date
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]
REGISTER = REPO_ROOT / ".trivyignore.yaml"

#: Advisory ids are pasted from scanner output into a shell command; keep them
#: to the shape every scanner actually emits.
VALID_ID = re.compile(r"^[A-Za-z][A-Za-z0-9._-]{2,63}$")


def ignored_ids(register: Path = REGISTER, today: date | None = None) -> list[str]:
    """Advisory ids whose accepted-risk entry has not expired."""
    if not register.exists():
        return []
    document = yaml.safe_load(register.read_text(encoding="utf-8")) or {}
    now = today or date.today()
    live: list[str] = []
    for entry in document.get("vulnerabilities") or []:
        advisory = str(entry.get("id", "")).strip()
        if not VALID_ID.match(advisory):
            print(f"skipping malformed register id {advisory!r}", file=sys.stderr)
            continue
        expiry = entry.get("expired_at")
        if isinstance(expiry, date) and expiry < now:
            print(
                f"{advisory}: accepted-risk entry expired on {expiry} — "
                "re-argue it or drop the entry; the audit will report it again",
                file=sys.stderr,
            )
            continue
        live.append(advisory)
    return live


def main() -> int:
    print(" ".join(f"--ignore-vuln {advisory}" for advisory in ignored_ids()))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

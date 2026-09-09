#!/usr/bin/env python3
"""Keep the configuration surface, ``.env.example`` and the docs honest.

Three artefacts describe the same settings and nothing tied them together: the
``BaseSettings`` classes that actually bind the environment, the ``.env.example``
template operators copy, and the reference page in the docs. The template had
silently drifted — it advertised ``VISION_ANTHROPIC_API_KEY`` while the field
bound bare ``ANTHROPIC_API_KEY``, so following the template set nothing.

The gate therefore:

- fails on any ``.env.example`` entry that no settings field and no source
  literal binds (a variable that does nothing when set);
- fails when the generated reference page is stale.

Run with ``--write`` to restamp the reference page after changing a setting.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from scripts.config_surface import (  # noqa: E402
    env_example_entries,
    env_literals,
    iter_settings,
    render_reference,
)

ENV_EXAMPLE = REPO_ROOT / ".env.example"
REFERENCE_PAGE = (
    REPO_ROOT / "mkdocs-site" / "docs" / "getting-started" / "configuration.md"
)

#: Families whose full name is built at runtime, so no literal exists to match:
#: ``BASELITH_FLAG_<FLAG>`` (core/feature_flags/manager.py) and
#: ``BASELITH_PROMPT_VARIANTS_<PROMPT>`` (core/prompts/catalog.py).
DYNAMIC_ENV_PREFIXES: tuple[str, ...] = (
    "BASELITH_FLAG_",
    "BASELITH_PROMPT_VARIANTS_",
)


def _is_bound(name: str, known: set[str]) -> bool:
    return name in known or name.startswith(DYNAMIC_ENV_PREFIXES)


def check_env_example(known: set[str]) -> list[str]:
    """Template entries that bind nothing, as ``path:line`` findings."""
    findings = []
    for name, line in sorted(env_example_entries(ENV_EXAMPLE).items()):
        if not _is_bound(name, known):
            findings.append(
                f".env.example:{line}: {name} binds no setting — "
                "the value is ignored when set"
            )
    return findings


def check_reference(expected: str) -> list[str]:
    """A single finding when the generated page is missing or stale."""
    if not REFERENCE_PAGE.exists():
        return [f"{REFERENCE_PAGE.relative_to(REPO_ROOT)}: missing — run --write"]
    if REFERENCE_PAGE.read_text(encoding="utf-8") != expected:
        return [
            f"{REFERENCE_PAGE.relative_to(REPO_ROOT)}: stale — "
            "settings changed without restamping; run --write"
        ]
    return []


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--write",
        action="store_true",
        help="regenerate the configuration reference page instead of checking it",
    )
    args = parser.parse_args()

    settings = iter_settings(REPO_ROOT)
    expected = render_reference(settings)

    if args.write:
        REFERENCE_PAGE.parent.mkdir(parents=True, exist_ok=True)
        REFERENCE_PAGE.write_text(expected, encoding="utf-8")
        print(
            f"✅ wrote {REFERENCE_PAGE.relative_to(REPO_ROOT)} "
            f"({len(settings)} settings)"
        )
        return 0

    known = {name for setting in settings for name in setting.names}
    known |= env_literals(REPO_ROOT)
    findings = check_env_example(known) + check_reference(expected)

    if findings:
        print("❌ configuration surface out of sync:\n")
        for finding in findings:
            print(f"  {finding}")
        print(
            f"\n{len(findings)} problem(s). Fix the binding, drop the entry, "
            "or run: python scripts/check_config_surface.py --write"
        )
        return 1

    print(f"✅ configuration surface consistent ({len(settings)} settings)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

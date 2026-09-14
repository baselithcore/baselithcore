#!/usr/bin/env python3
"""Keep the configuration surface, ``.env.example`` and the docs honest.

Three artefacts describe the same settings and nothing tied them together: the
``BaseSettings`` classes that actually bind the environment, the ``.env.example``
template operators copy, and the reference page in the docs. The template had
silently drifted — it advertised ``VISION_ANTHROPIC_API_KEY`` while the field
bound bare ``ANTHROPIC_API_KEY``, so following the template set nothing.

The gate therefore:

- fails on any entry in a shipped template (``.env.example`` and
  ``configs/.env.base``) that no settings field and no source literal binds —
  a variable that does nothing when set;
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

#: Every template the project tells an operator to copy. ``.env.example`` is
#: the root template; ``configs/.env.base`` is what ``baselith doctor`` prints
#: ("cp configs/.env.base .env") and what the baselithbot docs call the
#: reference template. Only the first was ever checked, so the second rotted
#: unobserved — it still advertised a removed OCR backend. A template nobody
#: validates is worse than no template: following it sets nothing and says so
#: to no one.
CHECKED_TEMPLATES: tuple[Path, ...] = (
    REPO_ROOT / ".env.example",
    REPO_ROOT / "configs" / ".env.base",
)
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


def known_env_names() -> set[str]:
    """Every environment variable name something in the repository binds.

    Returns:
        Settings-field names (aliases included) plus the upper-snake literals
        found in source, which together cover both declarative and
        ``os.environ``-read configuration.
    """
    settings = iter_settings(REPO_ROOT)
    return {name for setting in settings for name in setting.names} | env_literals(
        REPO_ROOT
    )


def check_template(template: Path, known: set[str]) -> list[str]:
    """Entries in one template that bind nothing, as ``path:line`` findings.

    Args:
        template: A shipped ``.env`` template.
        known: Every environment variable name the repository binds.

    Returns:
        One finding per dead entry. Empty when the template is clean or absent
        — a template that does not exist cannot mislead anyone.
    """
    if not template.exists():
        return []
    label = template.name
    return [
        f"{label}:{line}: {name} binds no setting — the value is ignored when set"
        for name, line in sorted(env_example_entries(template).items())
        if not _is_bound(name, known)
    ]


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

    # Call the helper rather than re-deriving inline: the regression test in
    # tests/unit/scripts/test_config_surface.py asserts against
    # known_env_names(), and two copies of this set would let the test drift
    # away from what the gate actually does.
    known = known_env_names()
    findings = [
        finding
        for template in CHECKED_TEMPLATES
        for finding in check_template(template, known)
    ] + check_reference(expected)

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

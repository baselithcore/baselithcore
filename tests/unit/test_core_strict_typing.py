"""Guard for the core strict-typing ratchet (scripts/check_core_strict_typing.py).

The allowlist is the gate: a package listed there is type-checked under the
strict flag set on every commit and in CI. These tests keep the list honest
(sorted, unique, pointing at real packages) and stop the flag set from being
quietly weakened.
"""

from __future__ import annotations

from pathlib import Path

from scripts.check_core_strict_typing import (
    REPO_ROOT,
    STRICT_CORE_PACKAGES,
    STRICT_FLAGS,
    all_core_packages,
    candidate_packages,
    package_path,
)


def test_allowlist_is_sorted_unique_existing_packages() -> None:
    packages = list(STRICT_CORE_PACKAGES)

    assert packages == sorted(set(packages)), "keep the allowlist sorted and unique"
    for name in packages:
        assert name.startswith("core."), name
        target = package_path(REPO_ROOT, name)
        assert target.is_file() or (target / "__init__.py").is_file(), name


def test_resilience_stays_strict() -> None:
    # The former check_core_resilience_typing.py gate lives on inside this one.
    assert "core.resilience" in STRICT_CORE_PACKAGES


def test_flag_set_is_not_weakened() -> None:
    for flag in (
        "--disallow-untyped-defs",
        "--disallow-incomplete-defs",
        "--warn-return-any",
        "--check-untyped-defs",
        "--no-implicit-optional",
    ):
        assert flag in STRICT_FLAGS, flag


def test_kernel_packages_are_strict() -> None:
    """The packages that run in every deployment stay covered.

    These are the ones a request touches on the way in and out — API, auth,
    config, storage, the agent loop and the LLM/vector services. Dropping one
    from the allowlist is a regression, not a cleanup.
    """
    for name in (
        "core.api",
        "core.auth",
        "core.cache",
        "core.chat",
        "core.config",
        "core.db",
        "core.di",
        "core.memory",
        "core.middleware",
        "core.observability",
        "core.orchestration",
        "core.plugins",
        "core.services.llm",
        "core.services.vectorstore",
    ):
        assert name in STRICT_CORE_PACKAGES, name


def test_partially_covered_parent_is_replaced_by_subpackages(tmp_path: Path) -> None:
    """``core.services`` never goes green as a whole; list its subpackages."""
    for dotted in (
        "core",
        "core/services",
        "core/services/llm",
        "core/services/vision",
    ):
        pkg = tmp_path / dotted
        pkg.mkdir(parents=True, exist_ok=True)
        (pkg / "__init__.py").write_text("", encoding="utf-8")

    names = all_core_packages(tmp_path, allowlist=("core.services.llm",))

    assert "core.services" not in names
    assert names == ["core.services.llm", "core.services.vision"]
    assert candidate_packages(tmp_path, allowlist=("core.services.llm",)) == [
        "core.services.vision"
    ]


def test_candidates_exclude_allowlisted_packages(tmp_path: Path) -> None:
    for name in ("alpha", "beta"):
        pkg = tmp_path / "core" / name
        pkg.mkdir(parents=True)
        (pkg / "__init__.py").write_text("", encoding="utf-8")
    (tmp_path / "core" / "__init__.py").write_text("", encoding="utf-8")
    (tmp_path / "core" / "context.py").write_text("", encoding="utf-8")

    assert candidate_packages(tmp_path, allowlist=("core.alpha",)) == [
        "core.beta",
        "core.context",
    ]
    assert package_path(tmp_path, "core.context") == tmp_path / "core" / "context.py"

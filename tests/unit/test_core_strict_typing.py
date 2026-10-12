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
        # The kernel, plus the public facade. ``baselith`` sits outside
        # ``baselith/core/`` — so it is never a ``--candidates`` suggestion — but it is
        # the package every downstream program imports, and it is strict from
        # its first commit.
        assert name == "baselith" or name.startswith("baselith.core."), name
        target = package_path(REPO_ROOT, name)
        assert target.is_file() or (target / "__init__.py").is_file(), name


def test_the_public_facade_is_strict() -> None:
    """Nothing downstream should meet a loosely typed symbol at the front door."""
    assert "baselith" in STRICT_CORE_PACKAGES


def test_resilience_stays_strict() -> None:
    # The former check_core_resilience_typing.py gate lives on inside this one.
    assert "baselith.core.resilience" in STRICT_CORE_PACKAGES


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
        "baselith.core.api",
        "baselith.core.auth",
        "baselith.core.cache",
        "baselith.core.chat",
        "baselith.core.config",
        "baselith.core.db",
        "baselith.core.di",
        "baselith.core.memory",
        "baselith.core.middleware",
        "baselith.core.observability",
        "baselith.core.orchestration",
        "baselith.core.plugins",
        "baselith.core.services.llm",
        "baselith.core.services.vectorstore",
    ):
        assert name in STRICT_CORE_PACKAGES, name


def test_partially_covered_parent_is_replaced_by_subpackages(tmp_path: Path) -> None:
    """``baselith.core.services`` never goes green as a whole; list its subpackages."""
    for dotted in (
        "baselith/core",
        "baselith/core/services",
        "baselith/core/services/llm",
        "baselith/core/services/vision",
    ):
        pkg = tmp_path / dotted
        pkg.mkdir(parents=True, exist_ok=True)
        (pkg / "__init__.py").write_text("", encoding="utf-8")

    names = all_core_packages(tmp_path, allowlist=("baselith.core.services.llm",))

    assert "baselith.core.services" not in names
    assert names == ["baselith.core.services.llm", "baselith.core.services.vision"]
    assert candidate_packages(tmp_path, allowlist=("baselith.core.services.llm",)) == [
        "baselith.core.services.vision"
    ]


def test_candidates_exclude_allowlisted_packages(tmp_path: Path) -> None:
    for name in ("alpha", "beta"):
        pkg = tmp_path / "baselith" / "core" / name
        pkg.mkdir(parents=True)
        (pkg / "__init__.py").write_text("", encoding="utf-8")
    (tmp_path / "baselith" / "core" / "__init__.py").write_text("", encoding="utf-8")
    (tmp_path / "baselith" / "core" / "context.py").write_text("", encoding="utf-8")

    assert candidate_packages(tmp_path, allowlist=("baselith.core.alpha",)) == [
        "baselith.core.beta",
        "baselith.core.context",
    ]
    assert (
        package_path(tmp_path, "baselith.core.context")
        == tmp_path / "baselith" / "core" / "context.py"
    )

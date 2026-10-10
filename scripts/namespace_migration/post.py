"""Deterministic regeneration after the rewrite (``--post``).

The rewrite makes import lines longer, which changes what the formatter and
the import sorter produce, and it changes module paths that generated
artefacts are stamped with. None of that is judgement: each step is a tool the
repository already pins, run over the whole tree. A step whose tool or script
is absent in the checkout is skipped and reported, so the same list serves
every repository that runs the codemod.
"""

from __future__ import annotations

import re
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class PostStep:
    """One regeneration command.

    Attributes:
        name: Label for the report.
        argv: Command; ``{python}`` is replaced by the running interpreter.
        requires: Repo-relative path that must exist for the step to run.
        ok_codes: Exit codes that count as success (a hook that rewrote
            files exits 1 and that is the point).
    """

    name: str
    argv: tuple[str, ...]
    requires: str
    ok_codes: tuple[int, ...] = (0,)


POST_STEPS: tuple[PostStep, ...] = (
    # Twice each: a fix can expose a format change and vice versa; the second
    # pass must find nothing, which is what makes the output a fixed point.
    PostStep(
        "ruff-check --fix",
        ("pre-commit", "run", "{ruff_lint}", "--all-files"),
        ".pre-commit-config.yaml",
        (0, 1),
    ),
    PostStep(
        "ruff-format",
        ("pre-commit", "run", "ruff-format", "--all-files"),
        ".pre-commit-config.yaml",
        (0, 1),
    ),
    PostStep(
        "ruff-check --fix (2)",
        ("pre-commit", "run", "{ruff_lint}", "--all-files"),
        ".pre-commit-config.yaml",
    ),
    PostStep(
        "ruff-format (2)",
        ("pre-commit", "run", "ruff-format", "--all-files"),
        ".pre-commit-config.yaml",
    ),
    PostStep(
        "config-surface --write",
        ("{python}", "scripts/check_config_surface.py", "--write"),
        "scripts/check_config_surface.py",
    ),
)


_RUFF_LINT_ID = re.compile(r"^\s*-\s*id:\s*(ruff-check|ruff)\s*$", re.M)


def _expand(arg: str, root: Path) -> str:
    """Resolve ``{python}`` and ``{ruff_lint}`` (the lint hook's id here).

    Upstream renamed the hook ``ruff`` -> ``ruff-check``; checkouts carry
    either spelling.
    """
    if arg == "{python}":
        return sys.executable
    if arg == "{ruff_lint}":
        config = root / ".pre-commit-config.yaml"
        text = config.read_text(encoding="utf-8") if config.is_file() else ""
        match = _RUFF_LINT_ID.search(text)
        return match.group(1) if match else "ruff-check"
    return arg


def run_post_steps(root: Path, *, git_add: bool = True) -> list[tuple[str, str]]:
    """Run every applicable step; return ``(step, outcome)`` pairs.

    pre-commit only sees files git knows about, so the migration is staged
    first when *git_add* is set: tracked changes (``git add -u``) plus the new
    ``baselith/`` tree (``git add -A -- baselith``, which honours
    ``.gitignore`` — a plugin's ``.env`` stays unstaged). Nothing else in the
    checkout is touched.
    """
    outcomes: list[tuple[str, str]] = []
    use_git = git_add and (root / ".git").exists() and shutil.which("git") is not None

    def stage() -> None:
        if use_git:
            subprocess.run(
                ["git", "-C", str(root), "add", "-u"], check=False, capture_output=True
            )
            subprocess.run(
                ["git", "-C", str(root), "add", "-A", "--", "baselith"],
                check=False,
                capture_output=True,
            )

    stage()
    for step in POST_STEPS:
        if not (root / step.requires).exists():
            outcomes.append((step.name, "skipped (not in this checkout)"))
            continue
        argv = [_expand(a, root) for a in step.argv]
        if shutil.which(argv[0]) is None and argv[0] != sys.executable:
            outcomes.append((step.name, f"skipped ({argv[0]} not on PATH)"))
            continue
        proc = subprocess.run(argv, cwd=root, capture_output=True, text=True)
        if "No hook with id" in proc.stdout + proc.stderr:
            verdict = "FAILED (no such pre-commit hook)"
        elif proc.returncode in step.ok_codes:
            verdict = "ok"
        else:
            verdict = f"FAILED (exit {proc.returncode})"
        outcomes.append((step.name, verdict))
        stage()
    return outcomes


__all__ = ["POST_STEPS", "PostStep", "run_post_steps"]

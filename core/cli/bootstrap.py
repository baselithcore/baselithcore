"""Make ``baselith`` run the checkout it was invoked from.

The ``baselith`` console script lives in the environment's ``bin/`` and its
``sys.path[0]`` is that ``bin/`` directory — **not** the current working
directory. So when a project checkout is newer than the installed
``baselith-core`` distribution, ``import core`` resolves to the *installed*
(stale) copy even though the operator is standing in the checkout.

That split is not merely cosmetic: ``plugins/__init__.py`` calls
``pkgutil.extend_path``, so as soon as anything puts the checkout on
``sys.path`` (the plugin-CLI scan in :mod:`core.cli.__main__` does, briefly),
``plugins.<name>`` resolves from the **checkout** while ``core.*`` keeps
coming from the **installed** distribution. A plugin written against a newer
core then fails with an ``ImportError`` for a symbol the old core lacks —
and when that import happens inside an RQ worker, RQ rewrites it into the
useless ``ValueError: Invalid attribute name: <job function>``.

The fix is to detect the mismatch once, at CLI start-up, and re-exec the
process with the checkout on ``PYTHONPATH`` so *both* trees come from the
same place. Re-exec (rather than a late ``sys.path.insert``) is what makes
this correct: by the time ``main()`` runs, ``core.cli`` and its imports are
already bound to the installed copy, and inserting a path would only affect
*subsequent* imports — yielding a process with two different cores loaded.

Escape hatches:

* ``BASELITH_CLI_NO_REEXEC=1`` — never re-exec (use the installed copy).
* ``_BASELITH_CLI_REEXEC=1`` — set on the child; the loop-breaker.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

REEXEC_MARKER = "_BASELITH_CLI_REEXEC"
"""Env flag set on the re-executed child so it never re-execs again."""

OPT_OUT = "BASELITH_CLI_NO_REEXEC"
"""Operator opt-out: run whatever ``import core`` resolves to."""

_CHECKOUT_MARKERS = (
    ("core", "__init__.py"),
    ("core", "cli", "__main__.py"),
    ("core", "_version.py"),
)


def find_checkout_root(cwd: Path | None = None) -> Path | None:
    """Return ``cwd`` when it is a Baselith source checkout, else ``None``.

    Args:
        cwd: Directory to inspect. Defaults to the process working directory.

    Returns:
        The checkout root, or ``None`` when the directory does not carry the
        marker files of a Baselith source tree.
    """
    root = (cwd or Path.cwd()).resolve()
    if all((root.joinpath(*parts)).is_file() for parts in _CHECKOUT_MARKERS):
        return root
    return None


def core_is_from(root: Path) -> bool:
    """Whether the imported ``core`` package lives under ``root``."""
    import core

    core_file = getattr(core, "__file__", None)
    if not core_file:
        return False
    try:
        return Path(core_file).resolve().is_relative_to(root)
    except (OSError, ValueError):  # pragma: no cover — defensive
        return False


def build_child_env(root: Path, env: dict[str, str] | None = None) -> dict[str, str]:
    """Environment for the re-executed child: ``root`` first on ``PYTHONPATH``."""
    child = dict(os.environ if env is None else env)
    existing = child.get("PYTHONPATH", "")
    parts = [p for p in existing.split(os.pathsep) if p]
    root_str = str(root)
    if root_str in parts:
        parts.remove(root_str)
    child["PYTHONPATH"] = os.pathsep.join([root_str, *parts])
    child[REEXEC_MARKER] = "1"
    return child


def should_reexec(root: Path | None) -> bool:
    """Whether a re-exec is both needed and allowed right now."""
    if root is None:
        return False
    if os.environ.get(REEXEC_MARKER) or os.environ.get(OPT_OUT):
        return False
    return not core_is_from(root)


def ensure_checkout_precedence(argv: list[str] | None = None) -> None:
    """Re-exec the CLI against the checkout in the working directory.

    A no-op in the common cases: not standing in a checkout, already running
    the checkout's ``core`` (editable install, ``python -m core.cli``), the
    child of a previous re-exec, or opted out. Any failure to re-exec is
    non-fatal — the CLI continues with the installed distribution, which is
    exactly the behaviour that existed before this shim.

    Args:
        argv: CLI arguments to forward. Defaults to ``sys.argv[1:]``.
    """
    root = find_checkout_root()
    if not should_reexec(root):
        return
    assert root is not None  # narrowed by should_reexec

    args = list(sys.argv[1:] if argv is None else argv)
    print(
        f"[baselith] running from the checkout at {root} "
        "(installed baselith-core would shadow it); re-executing",
        file=sys.stderr,
    )
    try:
        os.execve(
            sys.executable,
            [sys.executable, "-m", "core.cli", *args],
            build_child_env(root),
        )
    except OSError as exc:  # pragma: no cover — exec rarely fails
        print(f"[baselith] re-exec failed ({exc}); continuing", file=sys.stderr)


__all__ = [
    "OPT_OUT",
    "REEXEC_MARKER",
    "build_child_env",
    "core_is_from",
    "ensure_checkout_precedence",
    "find_checkout_root",
    "should_reexec",
]

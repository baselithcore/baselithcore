"""Interpreter-agnostic mypy invocation helper for the typing gate scripts.

The pre-commit hooks that wrap these scripts use ``language: system``, so the
``python`` they resolve depends entirely on the ``PATH`` of whatever shell (or
GUI git client) triggered the commit. When that interpreter is not the project
environment, ``sys.executable -m mypy`` fails with a bare
``No module named mypy`` even though mypy is installed and on ``PATH``.

:func:`mypy_base_command` resolves mypy in this order:

1. ``sys.executable -m mypy`` when the running interpreter provides it;
2. the first ``mypy`` executable found on ``PATH``;
3. otherwise :class:`MypyNotFoundError` is raised.
"""

from __future__ import annotations

import importlib.util
import shutil
import sys

__all__ = ["MypyNotFoundError", "mypy_base_command"]


class MypyNotFoundError(RuntimeError):
    """Raised when mypy is available neither to this interpreter nor on PATH."""


def mypy_base_command() -> list[str]:
    """Return the argv prefix that runs mypy.

    Returns:
        The command prefix to extend with mypy flags and target files.

    Raises:
        MypyNotFoundError: If no mypy installation can be located.
    """
    if importlib.util.find_spec("mypy") is not None:
        return [sys.executable, "-m", "mypy"]

    executable = shutil.which("mypy")
    if executable is not None:
        return [executable]

    raise MypyNotFoundError(
        f"mypy is not importable from {sys.executable} and no 'mypy' executable "
        "was found on PATH. Activate the project environment (or run "
        "'pip install -e \".[dev]\"') before committing."
    )

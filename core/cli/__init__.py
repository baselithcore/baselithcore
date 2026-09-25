"""
Baselith-Core CLI.

Provides command-line tools for project scaffolding and management.

``main`` is resolved on first access (see :mod:`core._lazy`). Importing it
eagerly here ran ``core.cli.__main__`` as a side effect of importing the
package, so ``python -m core.cli`` found the module already in
``sys.modules`` and runpy warned, and every ``import core.cli.<anything>``
paid for the whole command table.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Final

from core._lazy import lazy_exports

if TYPE_CHECKING:  # pragma: no cover - the eager view, for type checkers
    from core.cli.__main__ import main

_EXPORTS: Final[dict[str, str]] = {"main": "__main__"}

__getattr__ = lazy_exports(__name__, _EXPORTS)


def __dir__() -> list[str]:
    """The public names, so ``dir()`` sees the ones not yet resolved."""
    return sorted(__all__)


__all__ = ["main"]

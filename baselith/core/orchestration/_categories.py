"""The "undeclared" autonomy category — stdlib only, importable from anywhere.

A tool or connector action whose author never chose a category is treated as
``"destructive"``, the most restrictive one, so the approval gate errs on the
safe side. The typed ``Agent``'s standalone guard must not refuse such tools
— only the ones somebody explicitly marked destructive — so the default is a
``str`` that equals ``"destructive"`` in every respect but can be told apart
from a category somebody wrote down.
"""

from __future__ import annotations

from typing import Final


class UndeclaredCategory(str):
    """``"destructive"``, undeclared.

    Equal to, hashes like and serialises as the plain string, so every
    consumer of the category (approval matrix, ledger, tool spec) sees exactly
    ``"destructive"``.
    """

    __slots__ = ()


#: Default category of a ``ToolDefinition`` and a connector ``ActionSpec``.
UNDECLARED_DESTRUCTIVE: Final[str] = UndeclaredCategory("destructive")


def category_declared(category: str) -> bool:
    """Whether ``category`` was set by an author rather than defaulted.

    Args:
        category: A tool or action category.

    Returns:
        ``False`` only for :data:`UNDECLARED_DESTRUCTIVE` (or another
        :class:`UndeclaredCategory`).
    """
    return not isinstance(category, UndeclaredCategory)


__all__ = ["UNDECLARED_DESTRUCTIVE", "UndeclaredCategory", "category_declared"]

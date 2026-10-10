"""Workspace benchmark cases: a bug in one file of a small repository, judged by hidden tests.

Each case imports the fixed module by its real path, so the workspace staging, the import roots
and the read-only mount are exercised as a repository fix exercises them.
"""

from __future__ import annotations

from dataclasses import dataclass

from ..workspace import Workspace


@dataclass(frozen=True, slots=True)
class WorkspaceCase:
    """One repository bug and the tests the agent never sees."""

    name: str
    workspace: Workspace
    problem: str
    hidden: str


WORKSPACE_CASES: tuple[WorkspaceCase, ...] = (
    WorkspaceCase(
        "sibling_import",
        Workspace(
            {
                "pkg/util.py": "def double(x: int) -> int:\n    return 2 * x\n",
                "pkg/mod.py": (
                    "from pkg.util import double\n\n\n"
                    "def quad(x: int) -> int:\n    return double(x) + 1\n"
                ),
            },
            "pkg/mod.py",
            "tests/test_fix_mod.py",
        ),
        "quad(1) returns 3, expected 4 (quad multiplies by four)",
        "from pkg.mod import quad\n\n\ndef test_quad():\n"
        "    assert quad(1) == 4\n    assert quad(3) == 12\n    assert quad(0) == 0\n",
    ),
    WorkspaceCase(
        "src_layout",
        Workspace(
            {
                "src/shop/money.py": (
                    "def cents(amount: float) -> int:\n    return round(amount * 100)\n"
                ),
                "src/shop/cart.py": (
                    "from shop.money import cents\n\n\n"
                    "def total(items: list[dict]) -> int:\n"
                    '    """Total in cents; each item has a price and a qty."""\n'
                    '    return sum(cents(i["price"]) for i in items)\n'
                ),
            },
            "src/shop/cart.py",
            "tests/test_fix_cart.py",
            ("", "src"),
        ),
        "total ignores the quantity: two items at 2.50 should cost 500 cents, not 250",
        "from shop.cart import total\n\n\ndef test_total():\n"
        '    assert total([{"price": 2.5, "qty": 2}]) == 500\n'
        '    assert total([{"price": 1, "qty": 3}, {"price": 0.1, "qty": 1}]) == 310\n'
        "    assert total([]) == 0\n",
    ),
    WorkspaceCase(
        "class_method",
        Workspace(
            {
                "pkg/stack.py": (
                    "class Stack:\n"
                    "    def __init__(self) -> None:\n        self._items: list[int] = []\n\n"
                    "    def push(self, item: int) -> None:\n        self._items.append(item)\n\n"
                    "    def pop(self) -> int:\n        return self._items.pop(0)\n"
                ),
            },
            "pkg/stack.py",
            "tests/test_fix_stack.py",
        ),
        "Stack.pop returns the oldest item; a stack must return the newest (LIFO)",
        "import pytest\n\nfrom pkg.stack import Stack\n\n\ndef test_lifo():\n"
        "    s = Stack()\n    for i in (1, 2, 3):\n        s.push(i)\n"
        "    assert [s.pop(), s.pop(), s.pop()] == [3, 2, 1]\n\n\n"
        "def test_empty():\n    with pytest.raises(IndexError):\n        Stack().pop()\n",
    ),
)

__all__ = ["WORKSPACE_CASES", "WorkspaceCase"]

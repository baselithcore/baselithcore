"""Buggy snippets with hidden reference tests: the fix-rate benchmark.

The agent sees the code and the error a user would report, never ``hidden``. A case counts as
fixed only when the hidden tests pass on the code the agent returned, so a fix that merely
satisfies the agent's own tests does not count.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class FixCase:
    """One benchmark case."""

    name: str
    code: str
    error: str
    hidden: str


CASES: tuple[FixCase, ...] = (
    FixCase(
        "off_by_one",
        "def last(items: list[int]) -> int:\n    return items[len(items)]\n",
        "IndexError: list index out of range when calling last([1, 2, 3])",
        "from solution import last\n\n\ndef test_last():\n    assert last([1, 2, 3]) == 3\n"
        "    assert last([7]) == 7\n",
    ),
    FixCase(
        "wrong_operator",
        "def add(a: int, b: int) -> int:\n    return a - b\n",
        "add(2, 3) returns -1, expected 5",
        "from solution import add\n\n\ndef test_add():\n    assert add(2, 3) == 5\n"
        "    assert add(-1, 1) == 0\n",
    ),
    FixCase(
        "mutable_default",
        "def collect(item: int, bag: list[int] = []) -> list[int]:\n"
        "    bag.append(item)\n    return bag\n",
        "collect(1) then collect(2) returns [1, 2] the second time; each call should start empty",
        "from solution import collect\n\n\ndef test_fresh_each_call():\n"
        "    assert collect(1) == [1]\n    assert collect(2) == [2]\n"
        "    assert collect(3, [9]) == [9, 3]\n",
    ),
    FixCase(
        "integer_mean",
        "def mean(values: list[float]) -> float:\n    return sum(values) // len(values)\n",
        "mean([1, 2]) returns 1, expected 1.5",
        "import pytest\nfrom solution import mean\n\n\ndef test_mean():\n"
        "    assert mean([1, 2]) == pytest.approx(1.5)\n"
        "    assert mean([2, 4, 6]) == pytest.approx(4)\n",
    ),
    FixCase(
        "reverse_words",
        "def reverse_words(text: str) -> str:\n    return text[::-1]\n",
        "reverse_words('a b c') returns 'c b a' only by luck; reverse_words('ab cd') returns "
        "'dc ba', expected 'cd ab'",
        "from solution import reverse_words\n\n\ndef test_words():\n"
        "    assert reverse_words('ab cd') == 'cd ab'\n"
        "    assert reverse_words('one') == 'one'\n",
    ),
    FixCase(
        "mutating_merge",
        "def merged(a: dict[str, int], b: dict[str, int]) -> dict[str, int]:\n"
        "    a.update(b)\n    return a\n",
        "merged(x, y) changes x: the caller's dictionary is modified",
        "from solution import merged\n\n\ndef test_pure():\n    a, b = {'x': 1}, {'y': 2}\n"
        "    assert merged(a, b) == {'x': 1, 'y': 2}\n    assert a == {'x': 1}\n",
    ),
)

__all__ = ["CASES", "FixCase"]

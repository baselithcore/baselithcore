"""Every Python snippet a newcomer copies has to be valid Python.

The README's headline example and the quickstart's library section both used a
bare top-level ``await`` outside any function, so the first code anyone copied
from either surface raised ``SyntaxError`` on paste. Nothing caught it: the
snippets live in Markdown, ruff does not read them, and no test parsed them.

Parsing is all this asserts. Running the snippets would need a provider and an
API key, and would test the model rather than the documentation. A snippet
that parses can still be wrong; one that does not parse is wrong for every
reader, always.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]

#: Surfaces a newcomer copies from before they have a checkout.
QUICKSTART_PAGES = (
    "README.md",
    "mkdocs-site/docs/getting-started/quickstart.md",
    "mkdocs-site/docs/getting-started/index.md",
    "mkdocs-site/docs/index.md",
)

_FENCE = re.compile(r"```python\n(?P<code>.*?)```", re.DOTALL)


def _snippets(page: str) -> list[tuple[int, str]]:
    """Return ``(line_number, code)`` for each ```python fence on the page."""
    text = (REPO_ROOT / page).read_text(encoding="utf-8")
    found = []
    for match in _FENCE.finditer(text):
        line = text.count("\n", 0, match.start()) + 1
        found.append((line, match.group("code")))
    return found


@pytest.mark.parametrize("page", QUICKSTART_PAGES)
def test_every_python_snippet_parses(page: str) -> None:
    if not (REPO_ROOT / page).exists():
        pytest.skip(f"{page} not present")

    failures = []
    for line, code in _snippets(page):
        try:
            ast.parse(code)
        except SyntaxError as exc:
            failures.append(f"{page}:{line} — {exc.msg} (snippet line {exc.lineno})")

    assert not failures, "\n".join(failures)


@pytest.mark.parametrize("page", QUICKSTART_PAGES)
def test_no_snippet_awaits_outside_a_function(page: str) -> None:
    """``ast.parse`` accepts nothing at module level, but be explicit about why.

    A top-level ``await`` is the specific mistake these pages made, and it is
    the one that survives review because it reads correctly to anyone used to
    a notebook or a REPL. Naming it keeps the failure message actionable.
    """
    if not (REPO_ROOT / page).exists():
        pytest.skip(f"{page} not present")

    for line, code in _snippets(page):
        if re.search(r"^\s*(?:\w+\s*=\s*)?await\s", code, re.MULTILINE):
            tree = ast.parse(code)
            assert not any(
                isinstance(node, ast.Await)
                for statement in tree.body
                for node in ast.walk(statement)
                if isinstance(statement, ast.Expr | ast.Assign)
            ), (
                f"{page}:{line} awaits at module level; wrap it in an async "
                "function and call it with asyncio.run"
            )

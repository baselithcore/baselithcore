"""The lifespan freezes the startup heap, and only for as long as it serves.

Everything startup builds lives until shutdown, yet every full collection
re-walked it: ~140k tracked objects and a 16ms gen-2 pause on the built app,
0ms once frozen. The contract pinned here is positional, because the position
is the point: ``gc.collect()`` then ``gc.freeze()`` must be the last thing
before the lifespan yields (so the permanent generation holds everything
startup made and none of the garbage it left), and ``gc.unfreeze()`` must be
the first thing the ``finally`` does (so a process that runs several
lifespans — this test suite — is not left with a permanent generation).
"""

from __future__ import annotations

import ast
import inspect

from baselith.core.api import lifespan as lifespan_module


def _call_name(stmt: ast.stmt) -> str | None:
    """``"gc.freeze"`` for the statement ``gc.freeze()``; ``None`` otherwise."""
    if not isinstance(stmt, ast.Expr) or not isinstance(stmt.value, ast.Call):
        return None
    func = stmt.value.func
    if isinstance(func, ast.Attribute) and isinstance(func.value, ast.Name):
        return f"{func.value.id}.{func.attr}"
    return None


def _lifespan_body() -> list[ast.stmt]:
    tree = ast.parse(inspect.getsource(lifespan_module))
    for node in ast.walk(tree):
        if isinstance(node, ast.AsyncFunctionDef) and node.name == "lifespan":
            return node.body
    raise AssertionError("lifespan() not found")


def _yielding_try(body: list[ast.stmt]) -> ast.Try:
    for stmt in body:
        if isinstance(stmt, ast.Try) and any(
            isinstance(inner, ast.Expr) and isinstance(inner.value, ast.Yield)
            for inner in stmt.body
        ):
            return stmt
    raise AssertionError("the try/finally around `yield` not found")


def test_startup_ends_with_collect_then_freeze() -> None:
    body = _lifespan_body()
    position = body.index(_yielding_try(body))

    assert [_call_name(s) for s in body[position - 2 : position]] == [
        "gc.collect",
        "gc.freeze",
    ], "gc.collect() then gc.freeze() must be the last statements before yield"


def test_shutdown_unfreezes_first() -> None:
    finalbody = _yielding_try(_lifespan_body()).finalbody

    assert finalbody, "the yield must sit in a try/finally"
    assert _call_name(finalbody[0]) == "gc.unfreeze", (
        "gc.unfreeze() must be the first statement of the finally block"
    )

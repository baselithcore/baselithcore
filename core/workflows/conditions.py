"""
Safe workflow condition evaluation.

AST-interpreted condition expressions for workflow CONDITION nodes — no code
execution, only a whitelisted expression subset (comparisons, boolean ops,
arithmetic, attribute/subscript access, a few builtins).

Split out of ``executor.py`` to respect the module size cap.
"""

import ast
import operator
from typing import Any

# ---------------------------------------------------------------------------
# Safe expression evaluator (replaces bare code execution)
# ---------------------------------------------------------------------------

_SAFE_OPS = {
    ast.Eq: operator.eq,
    ast.NotEq: operator.ne,
    ast.Lt: operator.lt,
    ast.LtE: operator.le,
    ast.Gt: operator.gt,
    ast.GtE: operator.ge,
    ast.Is: operator.is_,
    ast.IsNot: operator.is_not,
    ast.In: lambda a, b: a in b,
    ast.NotIn: lambda a, b: a not in b,
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.And: None,  # handled specially
    ast.Or: None,
}


def _safe_condition(expression: str, variables: dict[str, Any]) -> bool:
    """Interpret a simple condition expression safely via AST.

    Supports: comparisons (==, !=, <, >, <=, >=, in, not in, is, is not),
    boolean operators (and, or, not), attribute access on provided variables,
    string/int/float/bool/None literals, and arithmetic (+, -, *).

    Raises ``ValueError`` for any unsupported AST node.
    """
    tree = ast.parse(expression.strip(), mode="eval")
    return bool(_ast_interpret(tree.body, variables))


def _ast_interpret(node: ast.AST, env: dict[str, Any]) -> Any:
    """
    Evaluate an AST node representing an expression against a variable environment.

    Args:
        node: The parsed Python AST node to evaluate.
        env: A dictionary of variable names mapped to their current values.

    Returns:
        The evaluated result of the expression.
    """
    if isinstance(node, ast.Expression):
        return _ast_interpret(node.body, env)
    if isinstance(node, ast.Constant):
        return node.value
    if isinstance(node, ast.Name):
        if node.id in env:
            return env[node.id]
        raise ValueError(f"Undefined variable: {node.id}")
    if isinstance(node, ast.Attribute):
        if node.attr.startswith("_"):
            raise ValueError(
                f"Access to private/dunder attribute denied: {node.attr!r}"
            )
        obj = _ast_interpret(node.value, env)
        return getattr(obj, node.attr)
    if isinstance(node, ast.Subscript):
        obj = _ast_interpret(node.value, env)
        key = _ast_interpret(node.slice, env)
        return obj[key]
    if isinstance(node, ast.Compare):
        left = _ast_interpret(node.left, env)
        for op_node, comparator in zip(node.ops, node.comparators):
            right = _ast_interpret(comparator, env)
            op_fn = _SAFE_OPS.get(type(op_node))
            if op_fn is None:
                raise ValueError(f"Unsupported comparison: {type(op_node).__name__}")
            if not op_fn(left, right):
                return False
            left = right
        return True
    if isinstance(node, ast.BoolOp):
        if isinstance(node.op, ast.And):
            return all(_ast_interpret(v, env) for v in node.values)
        if isinstance(node.op, ast.Or):
            return any(_ast_interpret(v, env) for v in node.values)
        raise ValueError(f"Unsupported boolean op: {type(node.op).__name__}")
    if isinstance(node, ast.UnaryOp):
        operand = _ast_interpret(node.operand, env)
        if isinstance(node.op, ast.Not):
            return not operand
        if isinstance(node.op, ast.USub):
            return -operand
        raise ValueError(f"Unsupported unary op: {type(node.op).__name__}")
    if isinstance(node, ast.BinOp):
        left = _ast_interpret(node.left, env)
        right = _ast_interpret(node.right, env)
        op_fn = _SAFE_OPS.get(type(node.op))
        if op_fn is None:
            raise ValueError(f"Unsupported binary op: {type(node.op).__name__}")
        return op_fn(left, right)
    if isinstance(node, ast.Call):
        # Only allow a small whitelist of builtins
        if isinstance(node.func, ast.Name) and node.func.id in (
            "len",
            "str",
            "int",
            "float",
            "bool",
        ):
            from collections.abc import Callable
            from typing import cast

            fn = cast(
                Callable,
                {"len": len, "str": str, "int": int, "float": float, "bool": bool}[
                    node.func.id
                ],
            )
            args = [_ast_interpret(a, env) for a in node.args]
            return fn(*args)
        raise ValueError(f"Function calls not allowed: {ast.dump(node.func)}")
    if isinstance(node, ast.IfExp):
        if _ast_interpret(node.test, env):
            return _ast_interpret(node.body, env)
        return _ast_interpret(node.orelse, env)
    raise ValueError(f"Unsupported expression node: {type(node).__name__}")


__all__ = ["_ast_interpret", "_safe_condition"]

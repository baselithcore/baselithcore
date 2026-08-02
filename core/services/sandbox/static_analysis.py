"""
Pre-execution static analysis for sandboxed code.

Second defense layer in front of the container policy: LLM-generated code is
AST-parsed before it reaches the sandbox. Syntax errors are rejected outright
(they would fail in the container anyway — rejecting here is earlier and
cheaper), and imports of flagged modules are surfaced per
``SandboxConfig.static_analysis_mode`` (warn-and-proceed by default, or
block).

Treat all analyzed code as untrusted input — the analysis itself only parses,
it never executes anything.
"""

from __future__ import annotations

import ast
from dataclasses import dataclass, field

from core.observability.logging import get_logger

logger = get_logger(__name__)


@dataclass
class StaticAnalysisReport:
    """Outcome of analyzing one code payload."""

    syntax_error: str | None = None
    flagged_imports: list[str] = field(default_factory=list)

    @property
    def parse_ok(self) -> bool:
        return self.syntax_error is None


def analyze_python(code: str, denied_imports: frozenset[str]) -> StaticAnalysisReport:
    """AST-analyze Python *code* without executing it.

    Args:
        code: The untrusted source to inspect.
        denied_imports: Top-level module names to flag (e.g. ``{"ctypes"}``).
            Submodule imports are flagged by their root (``socket.ssl`` →
            ``socket``); ``__import__``/``importlib`` string literals naming a
            flagged module are caught too.

    Returns:
        StaticAnalysisReport with the syntax error (if any) and the distinct
        flagged imports in source order.
    """
    try:
        tree = ast.parse(code)
    except SyntaxError as exc:
        return StaticAnalysisReport(syntax_error=str(exc))

    flagged: list[str] = []

    def _flag(module: str | None) -> None:
        root = (module or "").split(".", 1)[0]
        if root and root in denied_imports and root not in flagged:
            flagged.append(root)

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                _flag(alias.name)
        elif isinstance(node, ast.ImportFrom):
            _flag(node.module)
        elif isinstance(node, ast.Call):
            # __import__("mod") / importlib.import_module("mod")
            name = ""
            if isinstance(node.func, ast.Name):
                name = node.func.id
            elif isinstance(node.func, ast.Attribute):
                name = node.func.attr
            if name in {"__import__", "import_module"} and node.args:
                first = node.args[0]
                if isinstance(first, ast.Constant) and isinstance(first.value, str):
                    _flag(first.value)

    return StaticAnalysisReport(flagged_imports=flagged)


def parse_denied_imports(spec: str) -> frozenset[str]:
    """Parse the comma-separated config value into a module-name set."""
    return frozenset(m.strip() for m in spec.split(",") if m.strip())


__all__ = ["StaticAnalysisReport", "analyze_python", "parse_denied_imports"]

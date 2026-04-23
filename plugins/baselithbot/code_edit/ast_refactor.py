"""AST-aware Python refactors via ``libcst``.

Provides ``rename_symbol`` and ``extract_function`` operations. Both
require ``libcst`` to be installed; if not, ``ASTRefactorError`` is
raised. All file I/O flows through ``ScopedFileSystem`` so refactors
remain confined to the configured workspace root.
"""

from __future__ import annotations

import re
from typing import Any

from plugins.baselithbot.computer_use.filesystem import ScopedFileSystem


class ASTRefactorError(RuntimeError):
    """Raised when libcst is unavailable or a refactor fails."""


def _require_libcst() -> Any:
    try:
        import libcst  # type: ignore[import-not-found]
    except ImportError as exc:
        raise ASTRefactorError("libcst not installed; pip install libcst") from exc
    return libcst


async def rename_symbol(
    path: str,
    old_name: str,
    new_name: str,
    fs: ScopedFileSystem,
) -> dict[str, Any]:
    """Rename every occurrence of ``old_name`` (used as identifier) to ``new_name``.

    Honors Python identifier syntax: only matches whole-word identifiers,
    skipping substrings inside strings and comments via libcst when available.
    """
    if not old_name.isidentifier() or not new_name.isidentifier():
        raise ASTRefactorError("old_name and new_name must be Python identifiers")

    cst = _require_libcst()
    current = await fs.read(path)
    module = cst.parse_module(current["content"])

    class _Renamer(cst.CSTTransformer):  # type: ignore[misc,name-defined]
        renamed: int = 0

        def leave_Name(self, original_node, updated_node):  # type: ignore[no-untyped-def]  # noqa: N802 - libcst transformer visitor naming convention
            del original_node
            if updated_node.value == old_name:
                self.renamed += 1
                return updated_node.with_changes(value=new_name)
            return updated_node

    transformer = _Renamer()
    new_module = module.visit(transformer)
    write = await fs.write(path, new_module.code)
    return {
        "status": "success",
        "path": path,
        "renamed": transformer.renamed,
        "bytes_written": write["bytes_written"],
    }


async def extract_function(
    path: str,
    start_line: int,
    end_line: int,
    new_name: str,
    fs: ScopedFileSystem,
) -> dict[str, Any]:
    """Replace ``[start_line, end_line]`` with a call to a new top-level helper.

    Best-effort: detects free variables via a simple regex scan for bare
    identifiers and lifts them as ``new_name(arg1, arg2)`` parameters. Does
    not perform full data-flow analysis; suitable for short, self-contained
    blocks. Indentation is inferred from the first non-empty line.
    """
    if not new_name.isidentifier():
        raise ASTRefactorError("new_name must be a Python identifier")
    if end_line < start_line:
        raise ASTRefactorError("end_line must be >= start_line")

    current = await fs.read(path)
    lines = current["content"].splitlines(keepends=True)
    if start_line < 1 or end_line > len(lines):
        raise ASTRefactorError(
            f"line range out of bounds: {start_line}-{end_line} (file has {len(lines)})"
        )

    block = lines[start_line - 1 : end_line]
    if not block:
        raise ASTRefactorError("selected block is empty")

    indent = re.match(r"[ \t]*", block[0]).group(0)  # type: ignore[union-attr]
    body = "".join(line[len(indent) :] if line.startswith(indent) else line for line in block)

    identifiers = sorted(
        set(re.findall(r"\b[a-zA-Z_][a-zA-Z0-9_]*\b", body)).difference(_PYTHON_BUILTINS)
    )
    params = ", ".join(identifiers)
    helper_lines = [
        f"def {new_name}({params}):\n",
    ]
    for line in body.splitlines(keepends=True):
        helper_lines.append("    " + line if line.strip() else line)
    if helper_lines and not helper_lines[-1].endswith("\n"):
        helper_lines[-1] = helper_lines[-1] + "\n"
    helper_lines.append("\n")

    call = f"{indent}{new_name}({params})\n"
    new_lines = lines[: start_line - 1] + [call] + lines[end_line:]
    new_lines = helper_lines + new_lines

    write = await fs.write(path, "".join(new_lines))
    return {
        "status": "success",
        "path": path,
        "new_function": new_name,
        "params": identifiers,
        "bytes_written": write["bytes_written"],
    }


_PYTHON_BUILTINS = {
    "True",
    "False",
    "None",
    "and",
    "or",
    "not",
    "in",
    "is",
    "if",
    "else",
    "elif",
    "for",
    "while",
    "try",
    "except",
    "finally",
    "with",
    "as",
    "def",
    "class",
    "return",
    "yield",
    "import",
    "from",
    "raise",
    "pass",
    "break",
    "continue",
    "lambda",
    "global",
    "nonlocal",
    "self",
    "cls",
    "print",
    "len",
    "range",
    "int",
    "str",
    "float",
    "bool",
    "list",
    "dict",
    "set",
    "tuple",
    "type",
    "isinstance",
    "open",
    "min",
    "max",
    "sum",
    "abs",
    "any",
    "all",
    "enumerate",
    "zip",
    "sorted",
    "reversed",
    "map",
    "filter",
}


__all__ = ["rename_symbol", "extract_function", "ASTRefactorError"]

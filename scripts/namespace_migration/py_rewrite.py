"""libcst rewrite of one Python module.

Imports are rewritten unconditionally (an absolute ``import core…`` can only
mean the package that moves, unless a local package shadows it). String
literals and comments go through :mod:`text_rules`, which only rewrites what
the tree resolves. Bare ``"core"``/``"plugins"`` literals are rewritten only in
contexts that prove they are module names (``sys.modules[...]``,
``import_module(...)``, ``ModuleType(...)``, ``__package__ = ...``) or a path
component joined to the repository root; every other bare literal is listed.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from pathlib import PurePosixPath

import libcst as cst
from libcst.helpers import get_full_name_for_node
from libcst.metadata import (
    ImportAssignment,
    MetadataWrapper,
    PositionProvider,
    ScopeProvider,
)

from .config import KEEP_PRAGMA, NEW_ROOT, OLD_TOPS, TOOLING
from .py_contexts import ContextRules
from .py_paths import PathCollector
from .text_rules import TextContext, rewrite_literal, rewrite_text

_BARE_LITERAL = re.compile(r"""(?<![\w.])["'](core|plugins)["']""")
_BARE_SUBMODULE_IMPORT = re.compile(r"\bimport\s+[^#\n]*\b(?:core|plugins)\.", re.M)


def _dotted_expr(dotted: str) -> cst.Attribute | cst.Name:
    expr = cst.parse_expression(dotted)
    assert isinstance(expr, cst.Attribute | cst.Name)
    return expr


class NamespaceTransformer(ContextRules, cst.CSTTransformer):
    """Rewrite one module; see the module docstring for the rules."""

    def __init__(
        self,
        ctx: TextContext,
        *,
        plan_bump: set[int],
        plan_insert: set[int],
        rename_nodes: set[int],
        keep_lines: set[int],
        positions: dict[cst.CSTNode, int] | None,
    ) -> None:
        super().__init__()
        self.ctx = ctx
        self.tops = tuple(t for t in OLD_TOPS if t not in ctx.shadowed)
        self.plan_bump = plan_bump
        self.plan_insert = plan_insert
        self.rename_nodes = rename_nodes
        self.keep_lines = keep_lines
        self.positions = positions

    # -- helpers ------------------------------------------------------------
    def _kept(self, node: cst.CSTNode) -> bool:
        if not self.keep_lines or self.positions is None:
            return False
        return self.positions.get(node, -1) in self.keep_lines

    # -- imports ------------------------------------------------------------
    def leave_ImportFrom(
        self, original_node: cst.ImportFrom, updated_node: cst.ImportFrom
    ) -> cst.ImportFrom:
        if (
            updated_node.relative
            or updated_node.module is None
            or self._kept(original_node)
        ):
            return updated_node
        full = get_full_name_for_node(updated_node.module) or ""
        if full.split(".")[0] not in self.tops:
            return updated_node
        self.ctx.counts["import_from"] += 1
        return updated_node.with_changes(module=_dotted_expr(f"{NEW_ROOT}.{full}"))

    def leave_ImportAlias(
        self, original_node: cst.ImportAlias, updated_node: cst.ImportAlias
    ) -> cst.ImportAlias:
        full = get_full_name_for_node(updated_node.name) or ""
        top = full.split(".")[0]
        if top not in self.tops or (full == top and updated_node.asname is None):
            return updated_node
        if self._in_import_from:
            return updated_node
        cat = "import_as" if updated_node.asname else "import_submodule"
        self.ctx.counts[cat] += 1
        return updated_node.with_changes(name=_dotted_expr(f"{NEW_ROOT}.{full}"))

    _in_import_from = False

    def visit_ImportFrom(self, node: cst.ImportFrom) -> None:
        self._in_import_from = True

    def leave_ImportFrom_names(self, node: cst.ImportFrom) -> None:
        self._in_import_from = False

    def leave_SimpleStatementLine(
        self,
        original_node: cst.SimpleStatementLine,
        updated_node: cst.SimpleStatementLine,
    ) -> cst.SimpleStatementLine:
        body: list[cst.BaseSmallStatement] = []
        changed = False
        for stmt in updated_node.body:
            if not isinstance(stmt, cst.Import):
                body.append(stmt)
                continue
            bare = [
                a
                for a in stmt.names
                if a.asname is None and get_full_name_for_node(a.name) in self.tops
            ]
            if not bare:
                body.append(stmt)
                continue
            changed = True
            rest = [a for a in stmt.names if a not in bare]
            if rest:
                rest[-1] = rest[-1].with_changes(comma=cst.MaybeSentinel.DEFAULT)
                body.append(stmt.with_changes(names=rest))
            for alias in bare:
                self.ctx.counts["import_bare_top"] += 1
                body.append(
                    cst.ImportFrom(
                        module=cst.Name(NEW_ROOT),
                        names=[
                            cst.ImportAlias(
                                name=cst.Name(get_full_name_for_node(alias.name) or "")
                            )
                        ],
                    )
                )
        return updated_node.with_changes(body=body) if changed else updated_node

    def leave_Name(
        self, original_node: cst.Name, updated_node: cst.Name
    ) -> cst.BaseExpression:
        if id(original_node) in self.rename_nodes:
            self.ctx.counts["import_binding_use"] += 1
            return cst.Attribute(
                value=cst.Name(NEW_ROOT), attr=cst.Name(updated_node.value)
            )
        return updated_node

    # -- strings and comments -----------------------------------------------
    def leave_SimpleString(
        self, original_node: cst.SimpleString, updated_node: cst.SimpleString
    ) -> cst.SimpleString:
        value = updated_node.value
        prefix = updated_node.prefix
        if "b" in prefix.lower() or self._kept(original_node):
            return updated_node
        quote = updated_node.quote
        body = value[len(prefix) + len(quote) : -len(quote)]
        new = rewrite_literal(body, self.ctx)
        if new == body:
            return updated_node
        return updated_node.with_changes(value=f"{prefix}{quote}{new}{quote}")

    def leave_FormattedString(
        self, original_node: cst.FormattedString, updated_node: cst.FormattedString
    ) -> cst.FormattedString:
        if self._kept(original_node):
            return updated_node
        parts: list[cst.BaseFormattedStringContent] = []
        for i, part in enumerate(updated_node.parts):
            if isinstance(part, cst.FormattedStringText):
                text = part.value
                if i == 0 and text in ("core.", "plugins.") and text[:-1] in self.tops:
                    self.ctx.counts["fstring_head"] += 1
                    new = f"{NEW_ROOT}.{text}"
                else:
                    new = rewrite_text(text, self.ctx)
                part = part.with_changes(value=new) if new != text else part
            parts.append(part)
        return updated_node.with_changes(parts=parts)

    def leave_Comment(
        self, original_node: cst.Comment, updated_node: cst.Comment
    ) -> cst.Comment:
        if KEEP_PRAGMA in updated_node.value:
            return updated_node
        new = rewrite_text(updated_node.value, self.ctx)
        return (
            updated_node.with_changes(value=new)
            if new != updated_node.value
            else updated_node
        )


def _rename_targets(wrapper: MetadataWrapper, tops: Sequence[str]) -> set[int]:
    """Name nodes that read a binding made by ``import core.x`` (no alias)."""
    found: set[int] = set()
    scopes = wrapper.resolve(ScopeProvider)
    for scope in {s for s in scopes.values() if s is not None}:
        for assignment in scope.assignments:
            top = assignment.name.split(".")[0]
            if top not in tops or not isinstance(assignment, ImportAssignment):
                continue
            if not isinstance(assignment.node, cst.Import):
                continue
            for access in assignment.references:
                node = access.node
                while isinstance(node, cst.Attribute):
                    node = node.value
                if isinstance(node, cst.Name) and node.value == top:
                    found.add(id(node))
    return found


def rewrite_python(source: str, rel: str, ctx: TextContext, *, moves: bool) -> str:
    """Rewrite *source* (the content of repo-relative *rel*) and return it."""
    module = cst.parse_module(source)
    tops = tuple(t for t in OLD_TOPS if t not in ctx.shadowed)
    wrapper = MetadataWrapper(module, unsafe_skip_copy=True)
    rename: set[int] = set()
    if tops and _BARE_SUBMODULE_IMPORT.search(source):
        rename = _rename_targets(wrapper, tops)
    keep_lines: set[int] = set()
    positions: dict[cst.CSTNode, int] | None = None
    if KEEP_PRAGMA in source:
        keep_lines = {
            i for i, line in enumerate(source.splitlines(), 1) if KEEP_PRAGMA in line
        }
        positions = {
            node: pos.start.line
            for node, pos in wrapper.resolve(PositionProvider).items()
        }
    collector = PathCollector(
        dir_depth=len(PurePosixPath(rel).parts) - 1,
        moves=moves,
        file_class=TOOLING if ctx.tooling_like else ctx.file_class,
        fixture=ctx.repo_fixture,
    )
    module.visit(collector)
    transformer = NamespaceTransformer(
        ctx,
        plan_bump=collector.plan.bump_nodes,
        plan_insert=collector.plan.insert_nodes,
        rename_nodes=rename,
        keep_lines=keep_lines,
        positions=positions,
    )
    code = module.visit(transformer).code
    for lineno, line in enumerate(code.splitlines(), 1):
        for m in _BARE_LITERAL.finditer(line):
            if m.group(1) in tops and KEEP_PRAGMA not in line:
                ctx.unresolved.append(
                    ("bare_literal", f"L{lineno}: {line.strip()[:90]}")
                )
    return code


__all__ = ["NamespaceTransformer", "rewrite_python"]

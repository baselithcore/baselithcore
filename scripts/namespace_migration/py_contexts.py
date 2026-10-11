"""Context rules for bare ``"core"``/``"plugins"`` literals and path joins.

Split out of :mod:`py_rewrite` for the module size cap. A bare literal is
rewritten only where its context proves what it names: a module name
(``sys.modules[...]``, ``import_module(...)``, ``ModuleType(...)``,
``__package__ = ...``), a repository path in tooling (a root tuple, a
``*_ROOT`` assignment), or a join onto the repository root decided by
:class:`py_paths.PathCollector`.
"""

from __future__ import annotations

import re
from collections.abc import Sequence

import libcst as cst

from .config import NEW_ROOT
from .py_paths import bump, top_literal
from .text_rules import TextContext

#: Calls whose first argument is a module or logger name.
MODULE_CALLS: frozenset[str] = frozenset(
    {
        "import_module",
        "find_spec",
        "getLogger",
        "get_logger",
        "__import__",
        "ModuleType",
        "resolve_name",
        "import_string",
        "files",
    }
)
_SYS_MODULES_METHODS = frozenset({"get", "pop", "setdefault"})
#: Tooling names that hold a repository path (``SCANNED_ROOT``, ``PLUGINS_DIR``).
_PATHY_NAME = re.compile(r"(?i)(root|dir|path|tree)s?$")


def _is_sys_modules(node: cst.BaseExpression) -> bool:
    return (
        isinstance(node, cst.Attribute)
        and node.attr.value == "modules"
        and isinstance(node.value, cst.Name)
        and node.value.value == "sys"
    )


class ContextRules:
    """Mixin for :class:`py_rewrite.NamespaceTransformer` (shares its state)."""

    ctx: TextContext
    tops: tuple[str, ...]
    plan_bump: set[int]
    plan_insert: set[int]

    def _kept(self, node: cst.CSTNode) -> bool:  # overridden by the transformer
        return False

    def _bare(self, node: cst.BaseExpression) -> str | None:
        top = top_literal(node)
        return top if top in self.tops else None

    def _module_literal(self, node: cst.SimpleString, cat: str) -> cst.SimpleString:
        top = top_literal(node)
        assert top is not None
        self.ctx.counts[cat] += 1
        quote = node.value[-1]
        prefix = node.value[: node.value.index(quote)]
        return node.with_changes(value=f"{prefix}{quote}{NEW_ROOT}.{top}{quote}")

    def _tooling_path(self, node: cst.SimpleString) -> cst.SimpleString:
        top = self._bare(node)
        self.ctx.counts["tooling_root_literal"] += 1
        quote = node.value[-1]
        prefix = node.value[: node.value.index(quote)]
        return node.with_changes(value=f"{prefix}{quote}{NEW_ROOT}/{top}{quote}")

    def _tooling_collection(
        self, elements: Sequence[cst.BaseElement]
    ) -> list[cst.BaseElement] | None:
        if not self.ctx.tooling_like:
            return None
        changed = False
        out: list[cst.BaseElement] = []
        for el in elements:
            if (
                isinstance(el, cst.Element)
                and isinstance(el.value, cst.SimpleString)
                and self._bare(el.value)
            ):
                out.append(el.with_changes(value=self._tooling_path(el.value)))
                changed = True
            else:
                out.append(el)
        return out if changed else None

    def leave_Tuple(
        self, original_node: cst.Tuple, updated_node: cst.Tuple
    ) -> cst.Tuple:
        new = self._tooling_collection(updated_node.elements)
        return (
            updated_node.with_changes(elements=new) if new is not None else updated_node
        )

    def leave_List(self, original_node: cst.List, updated_node: cst.List) -> cst.List:
        new = self._tooling_collection(updated_node.elements)
        return (
            updated_node.with_changes(elements=new) if new is not None else updated_node
        )

    def leave_Set(self, original_node: cst.Set, updated_node: cst.Set) -> cst.Set:
        new = self._tooling_collection(updated_node.elements)
        return (
            updated_node.with_changes(elements=new) if new is not None else updated_node
        )

    # -- bare module-name literals ------------------------------------------
    def leave_Call(
        self, original_node: cst.Call, updated_node: cst.Call
    ) -> cst.BaseExpression:
        if id(original_node) in self.plan_bump:
            self.ctx.counts["path_parent_climb"] += 1
            return bump(updated_node)
        func = updated_node.func
        if (
            isinstance(func, cst.Attribute)
            and func.attr.value == "mkdir"
            and isinstance(original_node.func, cst.Attribute)
            and id(original_node.func.value) in self.plan_insert
            and not any(
                a.keyword and a.keyword.value == "parents" for a in updated_node.args
            )
        ):
            # ``(root / "core").mkdir()`` worked because root existed; the
            # inserted ``baselith`` level does not exist yet.
            self.ctx.counts["path_mkdir_parents"] += 1
            return updated_node.with_changes(
                args=[
                    *updated_node.args,
                    cst.Arg(
                        keyword=cst.Name("parents"),
                        value=cst.Name("True"),
                        equal=cst.AssignEqual(
                            whitespace_before=cst.SimpleWhitespace(""),
                            whitespace_after=cst.SimpleWhitespace(""),
                        ),
                    ),
                ]
            )
        if not updated_node.args or self._kept(original_node):
            return updated_node
        name = (
            func.value
            if isinstance(func, cst.Name)
            else (func.attr.value if isinstance(func, cst.Attribute) else "")
        )
        if (
            name in ("setitem", "delitem")
            and len(updated_node.args) >= 2
            and _is_sys_modules(updated_node.args[0].value)
        ):
            second = updated_node.args[1].value
            if isinstance(second, cst.SimpleString) and self._bare(second) is not None:
                new_second = self._module_literal(second, "module_name_sys_modules")
                args = list(updated_node.args)
                args[1] = args[1].with_changes(value=new_second)
                return updated_node.with_changes(args=args)
        first = updated_node.args[0].value
        if not isinstance(first, cst.SimpleString) or self._bare(first) is None:
            return updated_node
        is_sys_method = (
            isinstance(func, cst.Attribute)
            and name in _SYS_MODULES_METHODS
            and _is_sys_modules(func.value)
        )
        if name in MODULE_CALLS or is_sys_method:
            new_first = self._module_literal(first, "module_name_call")
        elif name in ("Path", "PurePath") and self.ctx.tooling_like:
            self.ctx.counts["path_call_literal"] += 1
            top = self._bare(first)
            new_first = first.with_changes(value=f'"{NEW_ROOT}/{top}"')
        else:
            return updated_node
        args = [
            updated_node.args[0].with_changes(value=new_first),
            *updated_node.args[1:],
        ]
        return updated_node.with_changes(args=args)

    def leave_Subscript(
        self, original_node: cst.Subscript, updated_node: cst.Subscript
    ) -> cst.BaseExpression:
        if id(original_node) in self.plan_bump:
            self.ctx.counts["path_parent_climb"] += 1
            return bump(updated_node)
        if _is_sys_modules(updated_node.value) and len(updated_node.slice) == 1:
            idx = updated_node.slice[0].slice
            if isinstance(idx, cst.Index) and isinstance(idx.value, cst.SimpleString):
                if self._bare(idx.value) is not None:
                    new = self._module_literal(idx.value, "module_name_sys_modules")
                    elem = updated_node.slice[0].with_changes(
                        slice=idx.with_changes(value=new)
                    )
                    return updated_node.with_changes(slice=[elem])
        return updated_node

    def leave_Attribute(
        self, original_node: cst.Attribute, updated_node: cst.Attribute
    ) -> cst.BaseExpression:
        if id(original_node) in self.plan_bump:
            self.ctx.counts["path_parent_climb"] += 1
            return bump(updated_node)
        return updated_node

    def leave_Comparison(
        self, original_node: cst.Comparison, updated_node: cst.Comparison
    ) -> cst.Comparison:
        left = updated_node.left
        if (
            isinstance(left, cst.SimpleString)
            and self._bare(left) is not None
            and len(updated_node.comparisons) == 1
            and isinstance(updated_node.comparisons[0].operator, cst.In | cst.NotIn)
            and _is_sys_modules(updated_node.comparisons[0].comparator)
        ):
            return updated_node.with_changes(
                left=self._module_literal(left, "module_name_sys_modules")
            )
        return updated_node

    def leave_Assign(
        self, original_node: cst.Assign, updated_node: cst.Assign
    ) -> cst.Assign:
        value = updated_node.value
        if not isinstance(value, cst.SimpleString) or self._bare(value) is None:
            return updated_node
        for target in updated_node.targets:
            t = target.target
            if isinstance(t, cst.Attribute) and t.attr.value in (
                "__package__",
                "__name__",
            ):
                return updated_node.with_changes(
                    value=self._module_literal(value, "module_name_dunder")
                )
            if (
                self.ctx.tooling_like
                and isinstance(t, cst.Name)
                and _PATHY_NAME.search(t.value)
            ):
                return updated_node.with_changes(value=self._tooling_path(value))
        return updated_node

    def leave_AnnAssign(
        self, original_node: cst.AnnAssign, updated_node: cst.AnnAssign
    ) -> cst.AnnAssign:
        value = updated_node.value
        if (
            self.ctx.tooling_like
            and isinstance(value, cst.SimpleString)
            and self._bare(value) is not None
            and isinstance(updated_node.target, cst.Name)
            and _PATHY_NAME.search(updated_node.target.value)
        ):
            return updated_node.with_changes(value=self._tooling_path(value))
        return updated_node

    def leave_BinaryOperation(
        self, original_node: cst.BinaryOperation, updated_node: cst.BinaryOperation
    ) -> cst.BaseExpression:
        if id(original_node) not in self.plan_insert:
            return updated_node
        self.ctx.counts["path_root_join"] += 1
        return updated_node.with_changes(
            left=cst.BinaryOperation(
                left=updated_node.left,
                operator=cst.Divide(
                    whitespace_before=cst.SimpleWhitespace(" "),
                    whitespace_after=cst.SimpleWhitespace(" "),
                ),
                right=cst.SimpleString(f'"{NEW_ROOT}"'),
            )
        )


__all__ = ["MODULE_CALLS", "ContextRules"]

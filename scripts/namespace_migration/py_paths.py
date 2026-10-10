"""Path expressions anchored on ``__file__`` that the move shifts by one level.

A module under ``core/`` that climbs to the repository root with
``Path(__file__).parents[2]`` lands one level short once it lives under
``baselith/core/``. The collector below finds every such climb (``parents[N]``,
a ``.parent`` chain, nested ``os.path.dirname``) and decides, per occurrence:

* in a file that moves and reaches the root or above → climb one more level,
  unless the very next path segment is ``"core"``/``"plugins"`` (then the
  shorter climb now lands on ``baselith/`` and the join is already right);
* anywhere, ``<repo root> / "core"`` → ``<repo root> / "baselith" / "core"``,
  where "repo root" is a root-reaching climb, a name bound to one, or (in
  tooling only) a conventionally named root variable.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import libcst as cst

from .config import NEW_ROOT, OLD_TOPS, TOOLING

#: Names that conventionally hold the repository root in tooling scripts.
ROOT_NAME_HEURISTIC: frozenset[str] = frozenset(
    {
        "ROOT",
        "REPO_ROOT",
        "REPO",
        "REPOSITORY_ROOT",
        "PROJECT_ROOT",
        "ROOT_DIR",
        "REPO_DIR",
        "_ROOT",
        "_REPO",
        "_REPO_ROOT",
        "_PROJECT_ROOT",
        "repo_root",
        "root",
        "project_root",
        "repo",
    }
)


#: Fixture names that hold a fake repository root in tooling tests.
FIXTURE_ROOT_NAMES: frozenset[str] = frozenset(
    {"tmp_path", "repo", "tree", "project", "base", "fake_repo", "repo_dir", "tmp"}
)


def _last_segment(node: cst.BaseExpression) -> str | None:
    """The literal last path segment of ``<expr> / "<seg>"``, if any."""
    if isinstance(node, cst.BinaryOperation) and isinstance(node.operator, cst.Divide):
        if isinstance(node.right, cst.SimpleString):
            try:
                value = node.right.evaluated_value
            except Exception:
                return None
            return value if isinstance(value, str) else None
    return None


def _is_file_name(node: cst.BaseExpression) -> bool:
    return isinstance(node, cst.Name) and node.value == "__file__"


def _call_name(node: cst.Call) -> str:
    func = node.func
    if isinstance(func, cst.Name):
        return func.value
    if isinstance(func, cst.Attribute):
        return func.attr.value
    return ""


def _is_path_of_file(node: cst.BaseExpression) -> bool:
    """``Path(__file__)`` optionally followed by ``.resolve()``/``.absolute()``."""
    if isinstance(node, cst.Call):
        name = _call_name(node)
        if name in ("Path", "PurePath") and node.args:
            return _is_file_name(node.args[0].value)
        if name in ("resolve", "absolute") and isinstance(node.func, cst.Attribute):
            return _is_path_of_file(node.func.value)
    return False


def climb_levels(node: cst.BaseExpression) -> int | None:
    """How many directories above the file's own directory *node* lands.

    ``Path(__file__).parent`` is 0 (the file's directory), ``.parents[2]`` is
    2, ``os.path.dirname(__file__)`` is 0. ``None`` for anything else.
    """
    if isinstance(node, cst.Subscript) and isinstance(node.value, cst.Attribute):
        attr = node.value
        if attr.attr.value == "parents" and len(node.slice) == 1:
            idx = node.slice[0].slice
            if (
                isinstance(idx, cst.Index)
                and isinstance(idx.value, cst.Integer)
                and _is_path_of_file(attr.value)
            ):
                return int(idx.value.value)
    if isinstance(node, cst.Attribute) and node.attr.value == "parent":
        if _is_path_of_file(node.value):
            return 0
        inner = climb_levels(node.value)
        return None if inner is None else inner + 1
    if isinstance(node, cst.Call) and _call_name(node) == "dirname" and node.args:
        arg = node.args[0].value
        if _is_file_name(arg) or (
            isinstance(arg, cst.Call)
            and _call_name(arg) in ("abspath", "realpath")
            and arg.args
            and _is_file_name(arg.args[0].value)
        ):
            return 0
        inner = climb_levels(arg)
        return None if inner is None else inner + 1
    return None


def bump(node: cst.BaseExpression) -> cst.BaseExpression:
    """Return *node* climbing exactly one directory further."""
    if isinstance(node, cst.Subscript):
        idx = node.slice[0].slice
        assert isinstance(idx, cst.Index) and isinstance(idx.value, cst.Integer)
        new_int = idx.value.with_changes(value=str(int(idx.value.value) + 1))
        new_slice = node.slice[0].with_changes(slice=idx.with_changes(value=new_int))
        return node.with_changes(slice=[new_slice])
    if isinstance(node, cst.Attribute):
        return cst.Attribute(value=node, attr=cst.Name("parent"))
    if isinstance(node, cst.Call):
        return node.with_changes(args=[cst.Arg(value=node)])
    return node


def top_literal(node: cst.BaseExpression) -> str | None:
    """``"core"``/``"plugins"`` when *node* is exactly that string literal."""
    if isinstance(node, cst.SimpleString):
        try:
            value = node.evaluated_value
        except Exception:
            return None
        if value in OLD_TOPS:
            return str(value)
    return None


@dataclass
class PathPlan:
    """Decisions the collector hands the transformer (keyed by node identity)."""

    bump_nodes: set[int] = field(default_factory=set)
    insert_nodes: set[int] = field(default_factory=set)


class PathCollector(cst.CSTVisitor):
    """Pre-pass deciding which climbs to bump and which joins to extend."""

    def __init__(
        self, *, dir_depth: int, moves: bool, file_class: str, fixture: bool = False
    ) -> None:
        self.fixture = fixture
        self.dir_depth = dir_depth
        self.moves = moves
        self.file_class = file_class
        self.plan = PathPlan()
        self._seen: set[int] = set()
        self._skip_bump: set[int] = set()
        self.root_names: set[str] = set()

    def _reach(self, node: cst.BaseExpression) -> int | None:
        """Depth below the repo root that *node* lands on (<=0: root or above)."""
        levels = climb_levels(node)
        return None if levels is None else self.dir_depth - levels

    def _is_root_expr(self, node: cst.BaseExpression) -> bool:
        reach = self._reach(node)
        if reach is not None:
            return reach == 0
        if isinstance(node, cst.Name):
            if node.value in self.root_names:
                return True
            if self.fixture and node.value in FIXTURE_ROOT_NAMES:
                return True
            return self.file_class == TOOLING and node.value in ROOT_NAME_HEURISTIC
        return False

    def visit_Assign(self, node: cst.Assign) -> None:
        if self._reach(node.value) == 0:
            for target in node.targets:
                if isinstance(target.target, cst.Name):
                    self.root_names.add(target.target.value)

    def visit_AnnAssign(self, node: cst.AnnAssign) -> None:
        if node.value is not None and self._reach(node.value) == 0:
            if isinstance(node.target, cst.Name):
                self.root_names.add(node.target.value)

    def visit_BinaryOperation(self, node: cst.BinaryOperation) -> None:
        if not isinstance(node.operator, cst.Divide) or top_literal(node.right) is None:
            return
        left = node.left
        if _last_segment(left) in (NEW_ROOT, *OLD_TOPS):
            # ``… / "baselith" / "core"`` is already migrated, and
            # ``… / "core" / "plugins"`` is core's own plugin machinery.
            return
        climbing = self._reach(left) is not None
        if climbing and self.moves and self._reach(left) == 0:
            # Shorter climb + "core" already lands on baselith/core.
            self._skip_bump.add(id(left))
            return
        if self._is_root_expr(left) or (self.fixture and not climbing):
            # In a tooling test every ``<dir> / "core"`` builds a fake
            # repository (or an installed site-packages) layout.
            self.plan.insert_nodes.add(id(node))

    def _consider_climb(self, node: cst.BaseExpression) -> bool:
        if id(node) in self._seen:
            return False
        reach = self._reach(node)
        if reach is None:
            return False
        self._mark_chain(node)
        if self.moves and reach <= 0 and id(node) not in self._skip_bump:
            self.plan.bump_nodes.add(id(node))
        return True

    def _mark_chain(self, node: cst.BaseExpression) -> None:
        cur: cst.BaseExpression | None = node
        while cur is not None and climb_levels(cur) is not None:
            self._seen.add(id(cur))
            if isinstance(cur, cst.Attribute):
                cur = cur.value
            elif isinstance(cur, cst.Call) and cur.args:
                cur = cur.args[0].value
            else:
                cur = None

    def visit_Subscript(self, node: cst.Subscript) -> None:
        self._consider_climb(node)

    def visit_Attribute(self, node: cst.Attribute) -> None:
        self._consider_climb(node)

    def visit_Call(self, node: cst.Call) -> None:
        self._consider_climb(node)


__all__ = [
    "FIXTURE_ROOT_NAMES",
    "ROOT_NAME_HEURISTIC",
    "PathCollector",
    "PathPlan",
    "bump",
    "climb_levels",
    "top_literal",
]

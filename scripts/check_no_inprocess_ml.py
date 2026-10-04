"""Fail when plugin code builds an ML model or a Qdrant client in-process.

Embedding, reranking and vector search are core services
(``core.services.inference``): a plugin calls them, it does not carry its own
``torch`` model or Qdrant connection. Every API process that mounts such a
plugin otherwise pays the model's full memory (multiples of GiB per worker)
and, with embedded Qdrant, a single-process file lock.

Flagged — a *call* (AST, so docstrings and comments never trip it) to any of::

    SentenceTransformer  CrossEncoder  BGEM3FlagModel  FlagReranker
    DocumentConverter    QdrantClient  AsyncQdrantClient
    transformers.AutoModel*  transformers.pipeline   (resolved through imports)

anywhere under ``plugins/`` outside test and build trees. A plugin that still
needs one (a vendored engine not yet migrated) is listed in
``configs/inprocess_ml_allowlist.yaml`` with the reason; an entry whose path no
longer exists, or no longer contains a flagged call, fails the gate so the
list can only shrink.

Usage:
    python scripts/check_no_inprocess_ml.py
"""

from __future__ import annotations

import ast
import sys
from dataclasses import dataclass
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]
ALLOWLIST_PATH = REPO_ROOT / "configs" / "inprocess_ml_allowlist.yaml"
SCANNED_ROOT = "plugins"

FORBIDDEN_CALLS = frozenset(
    {
        "SentenceTransformer",
        "CrossEncoder",
        "BGEM3FlagModel",
        "FlagReranker",
        "DocumentConverter",
        "QdrantClient",
        "AsyncQdrantClient",
    }
)
EXCLUDED_DIR_NAMES = frozenset(
    {
        "__pycache__",
        "node_modules",
        "tests",
        "test",
        "build",
        "dist",
        "target",
        ".venv",
        "venv",
        "site-packages",
    }
)


@dataclass(frozen=True)
class Violation:
    """One forbidden call."""

    path: str
    line: int
    name: str


#: Module-qualified constructors: flagged only when the call resolves to the
#: named module through an import, so ``redis.pipeline()`` or a local
#: ``pipeline`` never trips the gate.
FORBIDDEN_QUALIFIED: frozenset[tuple[str, str]] = frozenset(
    {
        ("transformers", "AutoModel"),
        ("transformers", "AutoModelForSequenceClassification"),
        ("transformers", "AutoModelForCausalLM"),
        ("transformers", "pipeline"),
    }
)
_QUALIFIED_MODULES = frozenset(module for module, _ in FORBIDDEN_QUALIFIED)


class _Imports:
    """What the file's import statements bind each local name to."""

    def __init__(self, tree: ast.AST) -> None:
        #: local name -> module it aliases (``import transformers as tf``)
        self.modules: dict[str, str] = {}
        #: local name -> (module, attribute) (``from x import Y as Z``)
        self.names: dict[str, tuple[str, str]] = {}
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    self.modules[alias.asname or alias.name.split(".")[0]] = alias.name
            elif isinstance(node, ast.ImportFrom) and node.module:
                for alias in node.names:
                    self.names[alias.asname or alias.name] = (node.module, alias.name)

    def resolve(self, func: ast.expr) -> tuple[str | None, str] | None:
        """``(module, attribute)`` the callee resolves to, or ``None``."""
        if isinstance(func, ast.Name):
            if func.id in self.names:
                return self.names[func.id]
            return (None, func.id)
        if isinstance(func, ast.Attribute):
            base = func.value
            if func.attr == "from_pretrained":
                # ``AutoModel.from_pretrained(...)``: the loader classmethod is
                # the constructor; resolve the class it hangs off.
                return self.resolve(base)
            if isinstance(base, ast.Name) and base.id in self.modules:
                return (self.modules[base.id], func.attr)
            return (None, func.attr)
        return None


def _forbidden_name(node: ast.Call, imports: _Imports) -> str | None:
    resolved = imports.resolve(node.func)
    if resolved is None:
        return None
    module, attr = resolved
    if attr in FORBIDDEN_CALLS:
        return attr
    root = (module or "").split(".")[0]
    if root in _QUALIFIED_MODULES and (root, attr) in FORBIDDEN_QUALIFIED:
        return f"{root}.{attr}"
    return None


def scan_file(path: Path, rel: str) -> list[Violation]:
    """Forbidden calls in one Python file (unparseable files are skipped).

    Import aliases are resolved, so ``from sentence_transformers import
    SentenceTransformer as ST; ST()`` and ``import transformers as t;
    t.pipeline()`` are both flagged.
    """
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"))
    except (SyntaxError, UnicodeDecodeError, OSError):
        return []
    imports = _Imports(tree)
    return [
        Violation(rel, node.lineno, name)
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and (name := _forbidden_name(node, imports))
    ]


def scan_tree(root: Path) -> list[Violation]:
    """Every violation under ``root/plugins``, in path order."""
    base = root / SCANNED_ROOT
    found: list[Violation] = []
    if not base.is_dir():
        return found
    for path in sorted(base.rglob("*.py")):
        rel_parts = path.relative_to(root).parts
        if any(part in EXCLUDED_DIR_NAMES for part in rel_parts[:-1]):
            continue
        found.extend(scan_file(path, "/".join(rel_parts)))
    return found


def load_allowlist(path: Path = ALLOWLIST_PATH) -> dict[str, str]:
    """``{path prefix: reason}``; every entry must carry a reason."""
    if not path.is_file():
        return {}
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    entries = data.get("allow") or []
    out: dict[str, str] = {}
    for entry in entries:
        prefix, reason = (
            str(entry.get("path", "")).strip(),
            str(entry.get("reason", "")).strip(),
        )
        if not prefix or not reason:
            raise ValueError(
                f"{path}: every entry needs 'path' and a non-empty 'reason'"
            )
        out[prefix.rstrip("/")] = reason
    return out


def _allowed(rel: str, allow: dict[str, str]) -> str | None:
    for prefix in allow:
        if rel == prefix or rel.startswith(prefix + "/"):
            return prefix
    return None


def check(root: Path = REPO_ROOT, allowlist: Path = ALLOWLIST_PATH) -> list[str]:
    """Human-readable problems; empty when the gate is green."""
    allow = load_allowlist(allowlist)
    violations = scan_tree(root)
    problems: list[str] = []
    used: set[str] = set()
    for v in violations:
        prefix = _allowed(v.path, allow)
        if prefix is None:
            problems.append(
                f"{v.path}:{v.line}: {v.name}( in a plugin — use core.services.inference "
                "(EmbeddingService / RerankService / ScopedVectorStore) instead"
            )
        else:
            used.add(prefix)
    for prefix in allow:
        if not (root / prefix).exists():
            problems.append(f"allowlist entry {prefix!r} no longer exists — delete it")
        elif prefix not in used:
            problems.append(
                f"allowlist entry {prefix!r} no longer contains a flagged call — delete it"
            )
    return problems


def main() -> int:
    problems = check()
    for line in problems:
        print(line, file=sys.stderr)
    if problems:
        print(f"\n{len(problems)} problem(s).", file=sys.stderr)
        return 1
    print(
        "no in-process ML model or Qdrant client under plugins/ (outside the allowlist)"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""Fail when plugin code holds its own LLM provider client, host or key resolver.

Model choice, provider credentials and failover are central: the framework's
funnel (``baselith.core.services.llm.get_llm_service``), its per-plugin policy
(``baselith.core.services.llm.resolve_governed_client_config``) and the credential seam
own them, and an operator pins a plugin's model from one console. A plugin that
imports a provider SDK, calls a provider's API host or registers its own key
resolver has a second, unreviewed path to a provider that the console, the cost
ledger and the failover chain never see.

Flagged (AST, so docstrings and comments never trip it) anywhere under
``baselith/plugins/`` outside test and build trees::

    import openai | anthropic | ollama          (also ``from X import ...``,
                                                 ``import X.sub``)
    "api.openai.com" / "api.anthropic.com"       in a string literal
    register_api_key_resolver                    imported, called or referenced

A plugin that still needs one (a vendored engine that bundles its own SDK
behind the governed-client seam) is listed in
``configs/plugin_llm_keys_allowlist.yaml`` with the reason; an entry whose path
no longer exists, or no longer contains a flagged construct, fails the gate so
the list can only shrink.

Usage:
    python scripts/check_no_plugin_llm_keys.py
"""

from __future__ import annotations

import ast
import sys
from dataclasses import dataclass
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]
ALLOWLIST_PATH = REPO_ROOT / "configs" / "plugin_llm_keys_allowlist.yaml"
SCANNED_ROOT = "baselith/plugins"

#: Provider SDKs a plugin must not import: the funnel owns the client.
FORBIDDEN_MODULES = frozenset({"openai", "anthropic", "ollama"})
#: Provider API hosts a plugin must not name in a string.
FORBIDDEN_HOSTS = ("api.openai.com", "api.anthropic.com")
#: The vision seam's own key resolver, a plugin-local credential path.
FORBIDDEN_NAMES = frozenset({"register_api_key_resolver"})

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
    """One flagged construct."""

    path: str
    line: int
    what: str


def _docstring_nodes(tree: ast.AST) -> set[int]:
    """``id`` of every string-constant node that is a docstring."""
    ids: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(
            node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)
        ):
            body = node.body
            if (
                body
                and isinstance(body[0], ast.Expr)
                and isinstance(body[0].value, ast.Constant)
                and isinstance(body[0].value.value, str)
            ):
                ids.add(id(body[0].value))
    return ids


def _forbidden_module(name: str) -> str | None:
    root = name.split(".")[0]
    return root if root in FORBIDDEN_MODULES else None


def scan_file(path: Path, rel: str) -> list[Violation]:
    """Flagged constructs in one Python file (unparseable files are skipped)."""
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"))
    except (SyntaxError, UnicodeDecodeError, OSError):
        return []
    docstrings = _docstring_nodes(tree)
    found: list[Violation] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if module := _forbidden_module(alias.name):
                    found.append(Violation(rel, node.lineno, f"import {module}"))
        elif isinstance(node, ast.ImportFrom):
            if (
                node.level == 0
                and node.module
                and (module := _forbidden_module(node.module))
            ):
                found.append(Violation(rel, node.lineno, f"import {module}"))
            for alias in node.names:
                if alias.name in FORBIDDEN_NAMES:
                    found.append(Violation(rel, node.lineno, alias.name))
        elif isinstance(node, ast.Name) and node.id in FORBIDDEN_NAMES:
            found.append(Violation(rel, node.lineno, node.id))
        elif isinstance(node, ast.Attribute) and node.attr in FORBIDDEN_NAMES:
            found.append(Violation(rel, node.lineno, node.attr))
        elif (
            isinstance(node, ast.Constant)
            and isinstance(node.value, str)
            and id(node) not in docstrings
        ):
            for host in FORBIDDEN_HOSTS:
                if host in node.value:
                    found.append(Violation(rel, node.lineno, host))
    return found


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
    problems: list[str] = []
    used: set[str] = set()
    for v in scan_tree(root):
        prefix = _allowed(v.path, allow)
        if prefix is None:
            problems.append(
                f"{v.path}:{v.line}: {v.what} in a plugin — route the call through "
                "baselith.core.services.llm (get_llm_service / resolve_governed_client_config) "
                "so the central policy, credential vault and cost ledger apply"
            )
        else:
            used.add(prefix)
    for prefix in allow:
        if not (root / prefix).exists():
            problems.append(f"allowlist entry {prefix!r} no longer exists — delete it")
        elif prefix not in used:
            problems.append(
                f"allowlist entry {prefix!r} no longer contains a flagged construct — delete it"
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
        "no plugin LLM provider SDK, API host or key resolver under baselith/plugins/ "
        "(outside the allowlist)"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

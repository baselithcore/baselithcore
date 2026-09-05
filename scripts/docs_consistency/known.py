"""What the codebase actually provides, for the docs checker to verify against.

Everything here reads the repository (files, settings classes, the CLI parser
tree, the FastAPI app) and returns plain sets so the checks stay cheap and
testable. Anything that needs the application importable is opt-in and
degrades to ``None`` when the environment cannot import it.
"""

from __future__ import annotations

import ast
import importlib
import os
import re
from pathlib import Path

ENV_LITERAL_RE = re.compile(r"[A-Z][A-Z0-9]*(?:_[A-Z0-9]+)+")

# Flags that mount optional routers; opened when building the app for the route check.
ROUTE_GATES = (
    "MCP_HTTP_TRANSPORT_ENABLED",
    "WEBHOOKS_ENABLED",
    "PRIVACY_ENABLED",
    "COMPLIANCE_ENABLED",
    "ENABLE_FEEDBACK",
    "ORCHESTRATOR_CHECKPOINT_ENABLED",
    "DOCS_ENABLED",
)

LITERAL_SCAN = (
    "core",
    "plugins",
    "scripts",
    "migrations",
    "configs",
    "deploy",
    ".github/workflows",
    ".env.example",
    "backend.py",
    "pyproject.toml",
    "prometheus.yml",
)
LITERAL_SCAN_GLOBS = ("docker-compose*.yml", "Dockerfile*")
LITERAL_SUFFIXES = frozenset(
    {".py", ".yaml", ".yml", ".toml", ".example", ".env", ".json"}
)
SKIP_DIRS = frozenset({"node_modules", "dist", "__pycache__", ".state", "ui"})


def _iter_scan_files(repo_root: Path):
    for pattern in LITERAL_SCAN_GLOBS:
        yield from sorted(repo_root.glob(pattern))
    for entry in LITERAL_SCAN:
        path = repo_root / entry
        if path.is_file():
            yield path
        elif path.is_dir():
            for file in path.rglob("*"):
                if file.is_file() and not (SKIP_DIRS & set(file.parts)):
                    if file.suffix in LITERAL_SUFFIXES or file.name == ".env.example":
                        yield file


def _settings_fields(tree: ast.AST) -> tuple[set[str], set[str]]:
    """``(env names, prefixes)`` derived from ``BaseSettings`` classes in ``tree``."""
    names: set[str] = set()
    prefixes: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.ClassDef):
            continue
        prefix = ""
        for stmt in node.body:
            if isinstance(stmt, ast.Assign) and any(
                isinstance(t, ast.Name) and t.id == "model_config" for t in stmt.targets
            ):
                value = stmt.value
                if isinstance(value, ast.Call):  # SettingsConfigDict(env_prefix=...)
                    for kw in value.keywords:
                        if kw.arg == "env_prefix" and isinstance(
                            kw.value, ast.Constant
                        ):
                            prefix = str(kw.value.value)
                elif isinstance(value, ast.Dict):  # {"env_prefix": "..."}
                    for key, val in zip(value.keys, value.values, strict=False):
                        if (
                            isinstance(key, ast.Constant)
                            and key.value == "env_prefix"
                            and isinstance(val, ast.Constant)
                        ):
                            prefix = str(val.value)
        if prefix:
            prefixes.add(prefix)
        for stmt in node.body:
            if isinstance(stmt, ast.AnnAssign) and isinstance(stmt.target, ast.Name):
                field = stmt.target.id
                if field.startswith("_") or field == "model_config":
                    continue
                names.add((prefix + field).upper())
                if not prefix:
                    names.add(field.upper())
    return names, prefixes


def known_env_names(repo_root: Path) -> tuple[set[str], tuple[str, ...]]:
    """Every env-var-looking token the code, templates and settings define."""
    names: set[str] = set()
    prefixes: set[str] = {"BASELITH_"}
    for file in _iter_scan_files(repo_root):
        try:
            text = file.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        names.update(ENV_LITERAL_RE.findall(text))
        if file.suffix == ".py" and "env_prefix" in text:
            try:
                field_names, field_prefixes = _settings_fields(ast.parse(text))
            except SyntaxError:
                continue
            names.update(field_names)
            prefixes.update(field_prefixes)
    return names, tuple(sorted(prefixes, key=len, reverse=True))


def cli_tree(repo_root: Path) -> dict[str, dict] | None:
    """``{command: {subcommand: {...}}}`` from the argparse tree, or ``None`` if the CLI cannot import."""
    import argparse

    try:
        cli_main = importlib.import_module("core.cli.__main__")
        commands: list[str] = list(cli_main.COMMANDS)
    except Exception:
        return None

    parser = argparse.ArgumentParser(prog="baselith", add_help=False)
    subparsers = parser.add_subparsers(dest="command")
    for cmd_name in commands:
        try:
            module = importlib.import_module(f"core.cli.commands.{cmd_name}")
            if hasattr(module, "register_parser"):
                module.register_parser(subparsers, argparse.HelpFormatter)
        except Exception:
            continue

    def walk(p: argparse.ArgumentParser) -> dict[str, dict]:
        tree: dict[str, dict] = {}
        for action in p._actions:
            if isinstance(action, argparse._SubParsersAction):
                for name, sub in action.choices.items():
                    tree[name] = walk(sub)
        return tree

    return walk(parser)


def known_routes(repo_root: Path) -> set[str] | None:
    """``METHOD /normalized/path`` for the app plus the routers the api_routers plugin mounts."""
    from scripts.docs_consistency.scan import normalize_route

    os.environ.setdefault("SECRET_KEY", "docs-consistency-check-" + "x" * 32)
    os.environ.setdefault("AUTH_REQUIRED", "false")
    # Feature-gated routers only exist when their flag is on; the docs
    # describe them, so build the app with every gate open.
    for gate in ROUTE_GATES:
        os.environ.setdefault(gate, "true")
    try:
        from fastapi import FastAPI

        from core.api.factory import create_app

        app = create_app()
    except Exception:
        return None

    routes: set[str] = set()

    def add_route(path: str, methods, kind: str) -> None:
        if kind == "Mount":
            routes.add(f"MOUNT {normalize_route(path)}")
            return
        norm = normalize_route(path)
        for method in methods or ("WS",):
            routes.add(f"{method} {norm}")

    def walk(route) -> None:
        # FastAPI >= 0.128 keeps include_router() lazy: an ``_IncludedRouter``
        # expands to effective route contexts (path already prefixed) or to
        # further included routers.
        if type(route).__name__ == "_IncludedRouter":
            for candidate in route.effective_candidates():
                if type(candidate).__name__ == "_IncludedRouter":
                    walk(candidate)
                else:
                    original = getattr(candidate, "original_route", None)
                    add_route(
                        getattr(candidate, "path", ""),
                        getattr(original, "methods", None),
                        type(original).__name__,
                    )
            return
        add_route(
            getattr(route, "path", ""),
            getattr(route, "methods", None),
            type(route).__name__,
        )

    def add_app(candidate) -> None:
        for route in candidate.routes:
            walk(route)

    add_app(app)

    # Routers core ships for the host to mount (documented, not auto-mounted).
    try:
        from core.a2a import create_a2a_router

        scratch = FastAPI()
        # The server is only touched inside the endpoints; the paths are what we need.
        scratch.include_router(create_a2a_router(None))  # type: ignore[arg-type]
        add_app(scratch)
    except Exception:
        pass

    api_routers = repo_root / "plugins" / "api_routers"
    for module_file in sorted(api_routers.glob("*.py")):
        if module_file.name.startswith("_") or module_file.name == "plugin.py":
            continue
        try:
            module = importlib.import_module(f"plugins.api_routers.{module_file.stem}")
        except Exception:
            continue
        router = getattr(module, "router", None)
        if router is None:
            continue
        # Nested include_router() calls stay lazy on an APIRouter; including
        # it into a throwaway app materialises every path with its prefix.
        scratch = FastAPI()
        try:
            scratch.include_router(router)
        except Exception:
            continue
        add_app(scratch)
    return routes


def import_ok(module: str, names: list[str]) -> str | None:
    """``None`` when ``from module import names`` works, else the reason."""
    try:
        mod = importlib.import_module(module)
    except Exception as exc:
        return f"cannot import {module}: {type(exc).__name__}: {exc}"
    missing = []
    for name in names:
        if name == "*" or not name:
            continue
        if hasattr(mod, name):
            continue
        try:
            importlib.import_module(f"{module}.{name}")
        except Exception:
            missing.append(name)
    return f"{module} has no attribute {', '.join(missing)}" if missing else None

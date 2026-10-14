"""Shared helpers for the documentation MCP server tests.

The server's package is called ``mcp``, which would collide with the MCP SDK of
the same name, so it is loaded under a private package name.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType

REPO_ROOT = Path(__file__).resolve().parents[3]
SERVER_DIR = REPO_ROOT / "mkdocs-site" / "mcp"
_PACKAGE = "_docs_mcp_under_test"


def load(module: str) -> ModuleType:
    """Import ``mkdocs-site/mcp/<module>.py`` as part of the private package."""
    if _PACKAGE not in sys.modules:
        spec = importlib.util.spec_from_file_location(
            _PACKAGE,
            SERVER_DIR / "__init__.py",
            submodule_search_locations=[str(SERVER_DIR)],
        )
        assert spec is not None and spec.loader is not None
        package = importlib.util.module_from_spec(spec)
        sys.modules[_PACKAGE] = package
        spec.loader.exec_module(package)
    return importlib.import_module(f"{_PACKAGE}.{module}")


def write_site(root: Path, nav: str, pages: dict[str, str]) -> None:
    """A minimal docs site: ``zensical.toml`` with ``nav`` plus the page files."""
    (root / "zensical.toml").write_text(nav, encoding="utf-8")
    for rel, text in pages.items():
        path = root / "docs" / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")


__all__ = ["REPO_ROOT", "SERVER_DIR", "load", "write_site"]

"""Every shipped manifest's ``entry_point`` must name a class that exists.

A manifest that carries ``entrypoint: __init__.py`` — the marketplace
spelling, where the field names the entry *file* — points at no class at all,
and a typo'd class name is no better. The loader reads the field as
``module:Class``, so either way the plugin fails to load at runtime.

No other gate sees it: the typing, boundary and contract gates never read
manifests, and a plugin's own tests rarely go through the loader. This one
does, purely statically (``ast``, no imports), so a manifest pointing at a
class nobody defines fails in the suite instead of on a deployment.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[4]
PLUGINS_ROOT = REPO_ROOT / "plugins"


def _manifest_paths() -> list[Path]:
    """Every plugin manifest shipped in this repository."""
    found: list[Path] = []
    for plugin_dir in sorted(PLUGINS_ROOT.iterdir()):
        if not plugin_dir.is_dir():
            continue
        for name in ("manifest.yaml", "manifest.yml", "manifest.json"):
            candidate = plugin_dir / name
            if candidate.exists():
                found.append(candidate)
                break
    return found


def _declared_entry_point(manifest: Path) -> str:
    """The manifest's ``entry_point`` (or its legacy ``entrypoint`` spelling)."""
    data = yaml.safe_load(manifest.read_text(encoding="utf-8")) or {}
    value = data.get("entry_point") or data.get("entrypoint") or ""
    return str(value).strip()


def _module_file(plugin_dir: Path, module_part: str) -> Path | None:
    """Resolve the module half of an entry point to a file on disk."""
    if not module_part:
        for name in ("plugin.py", "__init__.py"):
            candidate = plugin_dir / name
            if candidate.exists():
                return candidate
        return None
    relative = Path(*module_part.split("."))
    for candidate in (
        plugin_dir / f"{relative}.py",
        plugin_dir / relative / "__init__.py",
    ):
        if candidate.exists():
            return candidate
    return None


def _defines_class(module_file: Path, class_name: str) -> bool:
    """Whether ``module_file`` defines or re-exports ``class_name``."""
    tree = ast.parse(module_file.read_text(encoding="utf-8"), filename=str(module_file))
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef) and node.name == class_name:
            return True
        if isinstance(node, ast.ImportFrom) and any(
            alias.asname == class_name
            or (alias.asname is None and alias.name == class_name)
            for alias in node.names
        ):
            return True
    return False


MANIFESTS = _manifest_paths()


def test_repository_ships_plugin_manifests() -> None:
    """Guard the guard: an empty sweep would pass every assertion below."""
    assert len(MANIFESTS) >= 5


@pytest.mark.parametrize("manifest", MANIFESTS, ids=lambda p: p.parent.name)
def test_entry_point_names_a_class_that_exists(manifest: Path) -> None:
    declared = _declared_entry_point(manifest)
    if not declared:
        pytest.skip("no entry_point declared; the loader inspects the module")

    module_part, _, class_name = declared.rpartition(":")
    assert class_name.isidentifier(), (
        f"{manifest.parent.name}: entry_point {declared!r} is not 'module:Class'. "
        "A file name (the marketplace spelling) says nothing about the class — "
        "declare e.g. 'plugin:MyPlugin'."
    )

    plugin_dir = manifest.parent
    module_file = _module_file(plugin_dir, module_part.strip())
    assert module_file is not None, (
        f"{plugin_dir.name}: entry_point {declared!r} names module "
        f"{module_part!r}, which has no file in the plugin directory"
    )
    assert _defines_class(module_file, class_name), (
        f"{plugin_dir.name}: entry_point {declared!r} names class {class_name!r}, "
        f"which {module_file.relative_to(REPO_ROOT)} does not define"
    )

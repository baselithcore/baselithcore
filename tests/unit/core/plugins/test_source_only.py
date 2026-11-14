"""Plugin modules run from signed source, never from an unsigned bytecode cache."""

from __future__ import annotations

import importlib
import importlib.util
import py_compile
import sys
from pathlib import Path

import pytest

from baselith.core.plugins._source_only import (
    SourceOnlyLoader,
    install_source_only_finder,
    plugin_entry_spec,
)
from baselith.core.plugins.integrity import compute_plugin_hash

_PKG = "baselith.plugins.srconly_probe"


def _poison_cache(source: Path, payload: str) -> None:
    """Write an unchecked hash-based pyc for ``source`` compiled from ``payload``."""
    evil = source.with_name("_evil_src.py")
    evil.write_text(payload, encoding="utf-8")
    py_compile.compile(
        str(evil),
        cfile=importlib.util.cache_from_source(str(source)),
        dfile=str(source),
        invalidation_mode=py_compile.PycInvalidationMode.UNCHECKED_HASH,
        doraise=True,
    )
    evil.unlink()


@pytest.fixture
def plugin_dir(tmp_path: Path):
    root = tmp_path / "srconly_probe"
    root.mkdir()
    (root / "__init__.py").write_text("from .helper import VALUE\n", encoding="utf-8")
    (root / "helper.py").write_text("VALUE = 'signed'\n", encoding="utf-8")
    yield root
    for name in [n for n in sys.modules if n.startswith(_PKG)]:
        del sys.modules[name]


def _load(root: Path):
    import baselith.plugins  # noqa: F401 — parent package must exist

    spec = plugin_entry_spec(_PKG, root / "__init__.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[_PKG] = module
    spec.loader.exec_module(module)
    return module


def test_poisoned_cache_is_ignored_for_entry_and_submodules(plugin_dir: Path) -> None:
    before = compute_plugin_hash(plugin_dir)
    _poison_cache(plugin_dir / "__init__.py", "VALUE = 'unsigned-entry'\n")
    _poison_cache(plugin_dir / "helper.py", "VALUE = 'unsigned-helper'\n")
    # The premise of the attack: the caches do not move the signed digest.
    assert compute_plugin_hash(plugin_dir) == before

    module = _load(plugin_dir)

    assert module.VALUE == "signed"
    assert isinstance(sys.modules[f"{_PKG}.helper"].__spec__.loader, SourceOnlyLoader)


def test_stock_loader_would_have_run_the_poisoned_cache(plugin_dir: Path) -> None:
    """Guards the test itself: without the loader the poisoned pyc executes."""
    _poison_cache(plugin_dir / "helper.py", "VALUE = 'unsigned-helper'\n")
    spec = importlib.util.spec_from_file_location(
        "srconly_stock", plugin_dir / "helper.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert module.VALUE == "unsigned-helper"


def test_sourceless_bytecode_is_refused(plugin_dir: Path) -> None:
    ghost = plugin_dir / "ghost.py"
    ghost.write_text("VALUE = 'ghost'\n", encoding="utf-8")
    py_compile.compile(str(ghost), cfile=str(plugin_dir / "ghost.pyc"), doraise=True)
    ghost.unlink()
    _load(plugin_dir)

    with pytest.raises(ImportError, match="only as bytecode"):
        importlib.import_module(f"{_PKG}.ghost")


def test_nested_package_and_reload_stay_source_only(plugin_dir: Path) -> None:
    sub = plugin_dir / "sub"
    sub.mkdir()
    (sub / "__init__.py").write_text("", encoding="utf-8")
    (sub / "leaf.py").write_text("VALUE = 'leaf'\n", encoding="utf-8")
    _poison_cache(sub / "leaf.py", "VALUE = 'unsigned-leaf'\n")
    _load(plugin_dir)

    leaf = importlib.import_module(f"{_PKG}.sub.leaf")
    assert leaf.VALUE == "leaf"
    assert importlib.reload(leaf).VALUE == "leaf"


def test_install_is_idempotent_and_ignores_other_namespaces() -> None:
    install_source_only_finder()
    install_source_only_finder()
    finders = [f for f in sys.meta_path if type(f).__name__ == "_SourceOnlyFinder"]
    assert len(finders) == 1
    assert finders[0].find_spec("baselith.core.plugins.loader", ["/nowhere"]) is None
    assert finders[0].find_spec("baselith.plugins.x", None) is None

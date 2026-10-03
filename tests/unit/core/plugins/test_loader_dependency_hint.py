"""A plugin that relied on a library the core stopped installing gets told how
to get it back.

``defusedxml``, ``email-validator``, ``markdown-it-py``, ``networkx`` and
``sse-starlette`` left the core dependencies for the ``plugin-compat`` extra.
A third-party plugin that imported one without declaring it now fails with a
bare ``ModuleNotFoundError``; the load error names the extra and the manifest
field, so the operator fixes it without reading the release notes.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from core.plugins import PluginLoader, PluginRegistry
from core.plugins.load_gates import missing_dependency_hint


@pytest.fixture(autouse=True)
def _clean_sys_modules():
    before = set(sys.modules)
    yield
    for key in set(sys.modules) - before:
        if key.startswith("plugins."):
            del sys.modules[key]


def _write_plugin(root: Path, name: str, imported: str) -> Path:
    plugin_dir = root / name
    plugin_dir.mkdir(parents=True)
    (plugin_dir / "manifest.yaml").write_text(
        f"name: {name}\nversion: 1.0.0\n", encoding="utf-8"
    )
    (plugin_dir / "plugin.py").write_text(
        f"import {imported}\n\nfrom core.plugins import Plugin\n\n\n"
        "class P(Plugin):\n    pass\n",
        encoding="utf-8",
    )
    return plugin_dir


@pytest.mark.parametrize(
    ("module", "distribution"),
    [
        ("networkx", "networkx"),
        ("defusedxml", "defusedxml"),
        ("markdown_it", "markdown-it-py"),
        ("sse_starlette", "sse-starlette"),
        ("email_validator", "email-validator"),
    ],
)
def test_hint_names_the_extra_and_the_distribution(module: str, distribution: str):
    hint = missing_dependency_hint(ModuleNotFoundError(name=module))
    assert "baselith-core[plugin-compat]" in hint
    assert distribution in hint
    assert "python_dependencies" in hint


def test_submodule_import_is_recognised():
    hint = missing_dependency_hint(ModuleNotFoundError(name="networkx.algorithms"))
    assert "networkx" in hint


@pytest.mark.parametrize(
    "error",
    [
        ModuleNotFoundError(name="some_unrelated_lib"),
        ModuleNotFoundError("no name attribute"),
        ValueError("not an import error"),
    ],
)
def test_no_hint_for_anything_else(error: BaseException):
    assert missing_dependency_hint(error) == ""


@pytest.mark.asyncio
async def test_loader_error_carries_the_hint(tmp_path, monkeypatch):
    from core.plugins import loader as loader_module

    messages: list[str] = []
    monkeypatch.setattr(
        loader_module.logger,
        "error",
        lambda msg, *args, **kwargs: messages.append(msg % args if args else msg),
    )
    # A module name that is certainly not installed, routed through the same
    # table as the five real ones.
    monkeypatch.setitem(
        sys.modules["core.plugins.load_gates"].MOVED_TO_PLUGIN_COMPAT,
        "baselith_test_absent_mod",
        "baselith-test-absent",
    )
    plugin_dir = _write_plugin(tmp_path, "needs_absent", "baselith_test_absent_mod")

    loader = PluginLoader(tmp_path, PluginRegistry())
    assert await loader.load_plugin(plugin_dir, initialize=False) is None

    joined = "\n".join(messages)
    assert "baselith-core[plugin-compat]" in joined
    assert "baselith-test-absent" in joined

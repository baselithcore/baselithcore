"""Shared fixtures for the plugin-integrity tests.

`test_integrity.py` covers the verification policy and
`test_integrity_ui_surface.py` the shipped front-end bundle. Both need the same
throwaway plugin tree, the same hermetic environment and the same bundle
helper, so these live here instead of being imported across test modules.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import pytest


@pytest.fixture(autouse=True)
def _hermetic_integrity_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Integrity tests must not depend on ambient env / local .env flags.

    ``core.config`` loads the repository .env into os.environ once at import;
    a developer's ``BASELITH_SKIP_INTEGRITY_CHECK=true`` (dev escape hatch)
    would silently turn hash-mismatch tests into no-ops.
    """
    monkeypatch.delenv("BASELITH_SKIP_INTEGRITY_CHECK", raising=False)
    monkeypatch.delenv("BASELITH_REQUIRE_SIGNED_PLUGINS", raising=False)


@pytest.fixture
def plugin_dir(tmp_path: Path) -> Path:
    """Create a minimal plugin directory tree for hashing."""
    root = tmp_path / "demo_plugin"
    root.mkdir()
    (root / "manifest.yaml").write_text(
        "name: demo\nversion: 1.0.0\n", encoding="utf-8"
    )
    (root / "plugin.py").write_text("def hello(): return 'hi'\n", encoding="utf-8")
    sub = root / "skills"
    sub.mkdir()
    (sub / "__init__.py").write_text("", encoding="utf-8")
    (sub / "module.pyi").write_text("def stub(): ...\n", encoding="utf-8")
    return root


@pytest.fixture
def write_dist_bundle() -> Callable[..., Path]:
    """Write a compiled bundle under ``ui/dist`` and return its path.

    A plain helper in conftest is not importable from a test module — only
    fixtures are injected — so this hands back the callable instead.
    """

    def _write(plugin_dir: Path, body: str = "console.log(1)\n") -> Path:
        bundle = plugin_dir / "ui" / "dist" / "assets"
        bundle.mkdir(parents=True, exist_ok=True)
        target = bundle / "app.js"
        target.write_text(body, encoding="utf-8")
        return target

    return _write

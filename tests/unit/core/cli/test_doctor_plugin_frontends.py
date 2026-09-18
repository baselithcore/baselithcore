"""`baselith doctor` must look where the frontend builder actually writes.

The manifest contract documented for `baselith plugin add --docker` is
`path` (relative to the plugin) plus `output_dir` (relative to `path`), but
the doctor check read an undocumented `dist` key and otherwise fell back to
`frontend/dist` — so every plugin declaring the documented contract was
reported as "build missing" while its build sat, present, in `ui/dist`.
A diagnostic that fails the right answer teaches people to ignore diagnostics.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from core.cli.commands.doctor_plugin_checks import (
    check_plugin_frontends,
    frontend_build_output,
)


def _plugin(tmp_path: Path, name: str, frontend: object) -> Path:
    plugin_dir = tmp_path / "plugins" / name
    plugin_dir.mkdir(parents=True)
    (plugin_dir / "manifest.yaml").write_text(
        yaml.safe_dump({"name": name, "version": "1.0.0", "frontend": frontend}),
        encoding="utf-8",
    )
    return plugin_dir


def test_declared_contract_resolves_path_plus_output_dir(tmp_path: Path) -> None:
    plugin_dir = _plugin(tmp_path, "demo", {"path": "ui", "output_dir": "dist"})
    assert frontend_build_output(plugin_dir, {"path": "ui", "output_dir": "dist"}) == (
        plugin_dir / "ui" / "dist"
    )


def test_defaults_to_ui_dist_when_the_contract_omits_both(tmp_path: Path) -> None:
    plugin_dir = _plugin(tmp_path, "demo", {"package_manager": "npm"})
    assert frontend_build_output(plugin_dir, {"package_manager": "npm"}) == (
        plugin_dir / "ui" / "dist"
    )


def test_legacy_dist_key_stays_relative_to_the_plugin(tmp_path: Path) -> None:
    plugin_dir = _plugin(tmp_path, "demo", {"dist": "frontend/dist"})
    assert frontend_build_output(plugin_dir, {"dist": "frontend/dist"}) == (
        plugin_dir / "frontend" / "dist"
    )


def test_passes_when_the_declared_build_output_exists(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    plugin_dir = _plugin(
        tmp_path,
        "demo",
        {"path": "ui", "package_manager": "npm", "output_dir": "dist"},
    )
    (plugin_dir / "ui" / "dist").mkdir(parents=True)
    monkeypatch.chdir(tmp_path)
    result = check_plugin_frontends()
    assert result.passed, result.details


def test_reports_the_resolved_path_when_the_build_is_missing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _plugin(
        tmp_path,
        "demo",
        {"path": "ui", "package_manager": "pnpm", "build_command": "pnpm build"},
    )
    monkeypatch.chdir(tmp_path)
    result = check_plugin_frontends()
    assert not result.passed
    assert "demo:ui/dist" in result.details
    assert "pnpm build" in result.details

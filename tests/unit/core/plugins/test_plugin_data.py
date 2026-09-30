"""core.plugins.data_dir: per-plugin runtime state outside the plugin tree."""

from __future__ import annotations

from pathlib import Path

import pytest

from core.plugins.plugin_data import DATA_DIR_ENV, data_dir, plugin_data_root


def test_default_root_is_data_plugins_under_cwd(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv(DATA_DIR_ENV, raising=False)
    monkeypatch.chdir(tmp_path)
    path = data_dir("agent_jira")
    assert path == (tmp_path / "data" / "plugins" / "agent_jira").resolve()
    assert path.is_dir()


def test_env_root_wins(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(DATA_DIR_ENV, str(tmp_path / "state"))
    assert plugin_data_root() == (tmp_path / "state").resolve()
    assert data_dir("demo") == (tmp_path / "state" / "demo").resolve()


def test_relative_env_resolves_against_cwd_at_call_time(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(DATA_DIR_ENV, "rel/state")
    monkeypatch.chdir(tmp_path)
    assert (
        data_dir("demo", create=False)
        == (tmp_path / "rel" / "state" / "demo").resolve()
    )


def test_create_false_does_not_create(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(DATA_DIR_ENV, str(tmp_path))
    path = data_dir("demo", create=False)
    assert not path.exists()
    assert data_dir("demo") == path and path.is_dir()
    assert data_dir("demo") == path  # idempotent


@pytest.mark.parametrize(
    "bad",
    ["", ".", "..", "a/b", "a\\b", "../x", "-x", "_x", "a.b", " x", "x" * 65, "dé"],
)
def test_invalid_names_refused(
    bad: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(DATA_DIR_ENV, str(tmp_path))
    with pytest.raises(ValueError, match="invalid plugin name"):
        data_dir(bad)
    assert list(tmp_path.iterdir()) == []


def test_exported_from_core_plugins() -> None:
    import core.plugins

    assert core.plugins.data_dir is data_dir


def test_root_inside_plugins_dir_or_overlay_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import core.plugins.plugin_data as pd

    plugins = Path(pd.__file__).resolve().parents[2] / "plugins"
    monkeypatch.setenv(DATA_DIR_ENV, str(plugins / "x" / "state"))
    with pytest.raises(ValueError, match="plugins directory"):
        plugin_data_root()
    overlay = tmp_path / "overlay"
    monkeypatch.setenv("BASELITH_PLUGIN_OVERLAY_DIR", str(overlay))
    monkeypatch.setenv(DATA_DIR_ENV, str(overlay / ".store"))
    with pytest.raises(ValueError, match="plugin overlay"):
        data_dir("demo")

"""Baselithbot keeps its state outside the installed package."""

from __future__ import annotations

import stat
from pathlib import Path

import pytest

from plugins.baselithbot import state_paths


@pytest.fixture
def no_legacy(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(state_paths, "LEGACY_STATE_DIR", tmp_path / "no-legacy")
    monkeypatch.delenv(state_paths.STATE_DIR_ENV, raising=False)


@pytest.mark.usefixtures("no_legacy")
def test_default_is_per_user_data_dir_owner_only(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "xdg"))
    path = state_paths.resolve_state_dir()
    assert path == tmp_path / "xdg" / "baselith" / "baselithbot"
    assert stat.S_IMODE(path.stat().st_mode) == 0o700
    package_dir = Path(state_paths.__file__).resolve().parent
    assert not path.resolve().is_relative_to(package_dir)


@pytest.mark.usefixtures("no_legacy")
def test_home_fallback_without_xdg(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("XDG_DATA_HOME", raising=False)
    monkeypatch.setenv("HOME", str(tmp_path))
    assert (
        state_paths.user_state_dir() == tmp_path / ".local/share/baselith/baselithbot"
    )


@pytest.mark.usefixtures("no_legacy")
def test_env_override_wins(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(state_paths.STATE_DIR_ENV, str(tmp_path / "explicit"))
    assert state_paths.resolve_state_dir() == tmp_path / "explicit"
    assert (tmp_path / "explicit").is_dir()


def test_existing_legacy_dir_is_kept_with_deprecation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    legacy = tmp_path / ".state"
    legacy.mkdir()
    monkeypatch.setattr(state_paths, "LEGACY_STATE_DIR", legacy)
    monkeypatch.delenv(state_paths.STATE_DIR_ENV, raising=False)
    with pytest.warns(DeprecationWarning, match="deprecated"):
        assert state_paths.resolve_state_dir() == legacy


@pytest.mark.usefixtures("no_legacy")
def test_secret_key_file_is_owner_only(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from plugins.baselithbot.security.secret_store import ProviderSecretStore

    monkeypatch.delenv("BASELITHBOT_SECRET_KEY", raising=False)
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "xdg"))
    root = state_paths.resolve_state_dir()
    ProviderSecretStore(state_dir=root)
    assert stat.S_IMODE((root / ".secret_key").stat().st_mode) == 0o600

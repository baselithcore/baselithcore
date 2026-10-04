"""The ``.env`` of the directory the app runs from is loaded.

``load_project_env`` used to read only ``<checkout root>/.env``, resolved
from the package's own location. Installed from a wheel that is
``site-packages/.env``, which never exists, so the ``.env`` ``baselith init``
writes into a new project was ignored and the project refused to start for
want of the ``SECRET_KEY`` sitting in it.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from core.config import env as env_module

pytestmark = [pytest.mark.unit]


def test_cwd_env_comes_first(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(env_module, "PROJECT_ENV_FILE", tmp_path / "pkg" / ".env")

    assert env_module.env_file_candidates() == [
        tmp_path / ".env",
        tmp_path / "pkg" / ".env",
    ]


def test_same_file_is_listed_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(env_module, "PROJECT_ENV_FILE", tmp_path / ".env")

    assert env_module.env_file_candidates() == [tmp_path / ".env"]


def test_cwd_value_wins_and_real_env_wins_over_both(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    names = ("BASELITH_TEST_ENV_A", "BASELITH_TEST_ENV_B", "BASELITH_TEST_ENV_C")
    for name in names:
        monkeypatch.delenv(name, raising=False)
    (tmp_path / "pkg").mkdir()
    (tmp_path / ".env").write_text(
        "BASELITH_TEST_ENV_A=project\nBASELITH_TEST_ENV_C=project\n"
    )
    (tmp_path / "pkg" / ".env").write_text(
        "BASELITH_TEST_ENV_A=checkout\nBASELITH_TEST_ENV_B=checkout\n"
    )
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(env_module, "PROJECT_ENV_FILE", tmp_path / "pkg" / ".env")
    monkeypatch.setattr(env_module, "_env_loaded", False)
    monkeypatch.setenv("BASELITH_TEST_ENV_C", "real")

    try:
        env_module.load_project_env()

        assert os.environ["BASELITH_TEST_ENV_A"] == "project"
        assert os.environ["BASELITH_TEST_ENV_B"] == "checkout"
        assert os.environ["BASELITH_TEST_ENV_C"] == "real"
    finally:
        for name in ("BASELITH_TEST_ENV_A", "BASELITH_TEST_ENV_B"):
            os.environ.pop(name, None)


@pytest.mark.skipif(os.name != "posix", reason="POSIX ownership and modes")
class TestUntrustedEnvFile:
    """A .env another local user could write never feeds the settings."""

    def test_missing_file_is_fine(self, tmp_path: Path) -> None:
        assert env_module.is_trusted_env_file(tmp_path / ".env")

    def test_private_file_is_trusted(self, tmp_path: Path) -> None:
        path = tmp_path / ".env"
        path.write_text("X=1\n")
        path.chmod(0o600)
        assert env_module.is_trusted_env_file(path)

    @pytest.mark.parametrize("mode", [0o620, 0o602, 0o666])
    def test_group_or_world_writable_is_refused(
        self, tmp_path: Path, mode: int, caplog: pytest.LogCaptureFixture
    ) -> None:
        path = tmp_path / ".env"
        path.write_text("X=1\n")
        path.chmod(mode)
        with caplog.at_level("WARNING", logger="core.config.env"):
            assert not env_module.is_trusted_env_file(path)
        assert "writable" in caplog.text

    def test_foreign_owner_is_refused(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        path = tmp_path / ".env"
        path.write_text("X=1\n")
        path.chmod(0o600)
        owner = path.stat().st_uid
        monkeypatch.setattr(env_module.os, "geteuid", lambda: owner + 1)
        if owner == 0:
            pytest.skip("root-owned files are trusted by design")
        assert not env_module.is_trusted_env_file(path)

    def test_refused_file_is_not_loaded(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("BASELITH_TEST_ENV_UNTRUSTED", raising=False)
        path = tmp_path / ".env"
        path.write_text("BASELITH_TEST_ENV_UNTRUSTED=attacker\n")
        path.chmod(0o666)
        monkeypatch.chdir(tmp_path)
        monkeypatch.setattr(env_module, "PROJECT_ENV_FILE", tmp_path / ".env")
        monkeypatch.setattr(env_module, "_env_loaded", False)
        env_module.load_project_env()
        assert "BASELITH_TEST_ENV_UNTRUSTED" not in os.environ

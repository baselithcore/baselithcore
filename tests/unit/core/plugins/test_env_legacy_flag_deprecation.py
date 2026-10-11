"""``BASELITH_PLUGIN_ENV_LEGACY_DENYLIST`` has a removal date: 2.0.

The flag was deprecated on introduction; these pin that setting it tells the
operator *when* it goes away — once per process, whether or not a plugin
actually depends on it — and that the per-plugin refusal warning names the
same version.
"""

from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from baselith.core.plugins import _env
from baselith.core.plugins._env import (
    LEGACY_DENYLIST_ONLY_FLAG,
    LEGACY_DENYLIST_REMOVED_IN,
    apply_plugin_env,
)

FOREIGN_KEY = "LEGACYFLAGTEST_FOREIGN_TOKEN"


@pytest.fixture
def logger(monkeypatch: pytest.MonkeyPatch) -> MagicMock:
    fake = MagicMock()
    monkeypatch.setattr(_env, "logger", fake)
    monkeypatch.setattr(_env, "_legacy_flag_warned", False)
    return fake


def _warnings_text(logger: MagicMock) -> list[str]:
    return [call.args[0] % call.args[1:] for call in logger.warning.call_args_list]


def _plugin(tmp_path: Path, body: str) -> Path:
    plugin_dir = tmp_path / "flagplugin"
    plugin_dir.mkdir()
    env = plugin_dir / ".env"
    env.write_text(body, encoding="utf-8")
    return env


def test_removal_version_is_2_0() -> None:
    assert LEGACY_DENYLIST_REMOVED_IN == "2.0"


def test_setting_the_flag_logs_the_removal_once(
    tmp_path: Path, logger: MagicMock, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(LEGACY_DENYLIST_ONLY_FLAG, "true")
    # Only in-namespace keys: the flag changes nothing for this plugin, and
    # the operator must still learn that it is going away.
    env = _plugin(tmp_path, "FLAGPLUGIN_SETTING=1\n")
    monkeypatch.delenv("FLAGPLUGIN_SETTING", raising=False)
    try:
        apply_plugin_env(env, "flagplugin", {})
        apply_plugin_env(env, "flagplugin", {})
    finally:
        os.environ.pop("FLAGPLUGIN_SETTING", None)

    deprecations = [w for w in _warnings_text(logger) if "deprecated" in w]
    assert len(deprecations) == 1
    assert LEGACY_DENYLIST_ONLY_FLAG in deprecations[0]
    assert "removed in 2.0" in deprecations[0]


def test_refusal_warning_names_the_removal_version(
    tmp_path: Path, logger: MagicMock, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(LEGACY_DENYLIST_ONLY_FLAG, "1")
    monkeypatch.delenv(FOREIGN_KEY, raising=False)
    env = _plugin(tmp_path, f"{FOREIGN_KEY}=x\n")
    try:
        apply_plugin_env(env, "flagplugin", {})
        assert os.environ.get(FOREIGN_KEY) == "x"
    finally:
        os.environ.pop(FOREIGN_KEY, None)

    refusal = [w for w in _warnings_text(logger) if "out-of-namespace" in w]
    assert len(refusal) == 1
    assert "will be removed in 2.0" in refusal[0]


@pytest.mark.parametrize("value", ["", "false", "0", "no"])
def test_unset_or_falsy_flag_logs_no_deprecation(
    tmp_path: Path, logger: MagicMock, monkeypatch: pytest.MonkeyPatch, value: str
) -> None:
    monkeypatch.setenv(LEGACY_DENYLIST_ONLY_FLAG, value)
    monkeypatch.delenv(FOREIGN_KEY, raising=False)
    env = _plugin(tmp_path, f"{FOREIGN_KEY}=x\n")
    apply_plugin_env(env, "flagplugin", {})

    assert FOREIGN_KEY not in os.environ
    assert not [w for w in _warnings_text(logger) if "deprecated" in w]

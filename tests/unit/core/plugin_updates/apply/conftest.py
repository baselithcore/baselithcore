"""The executor fixture shared by the executor tests."""

from __future__ import annotations

from pathlib import Path

import pytest

from ._executor_env import Env, build_env


@pytest.fixture()
def env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Env:
    monkeypatch.setattr(
        "core.plugin_updates.apply.staging._git_ls_files",
        lambda d: ["manifest.yaml", "__init__.py"],
    )
    return build_env(tmp_path)

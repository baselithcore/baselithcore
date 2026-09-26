"""A plugin's exported ``.env`` keys belong to the plugin, not to the core.

The config drift check compares every environment variable against the core
settings tree. A key a plugin's ``.env`` exported in its own namespace —
``ACME_PROJECT_PLANNER_ENABLE_TEST_CASES`` — is one prefix away from a
core setting and was reported as a typo. The loader now
registers what it exports, so the check leaves it alone.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from core.config import drift
from core.plugins._env import apply_plugin_env

NS_KEY = "NSPLUGIN_PROJECT_PLANNER_ENABLE_TEST_CASES"
FOREIGN_KEY = "PROJECT_PLANER_ENABLE_TEST_CASES"


@pytest.fixture(autouse=True)
def _isolated(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(drift, "_OWNED_ENV", set())
    monkeypatch.delenv(NS_KEY, raising=False)
    monkeypatch.delenv(FOREIGN_KEY, raising=False)


def test_exported_keys_are_registered_as_owned(tmp_path: Path) -> None:
    plugin_dir = tmp_path / "nsplugin"
    plugin_dir.mkdir()
    env_file = plugin_dir / ".env"
    env_file.write_text(f"{NS_KEY}=false\n{FOREIGN_KEY}=false\n", encoding="utf-8")

    apply_plugin_env(env_file, "nsplugin", {})

    environ = {NS_KEY: "false", FOREIGN_KEY: "false"}
    # The in-namespace key is the plugin's; the out-of-namespace one was never
    # exported, so a near-miss there is still worth reporting.
    assert [s.name for s in drift.suspected_typos(environ)] == [FOREIGN_KEY]

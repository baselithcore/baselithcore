"""Shared fixtures for Baselithbot plugin tests.

Baselithbot pulls in heavy optional deps (playwright, pyautogui, mss). Tests
in this package run on top of the stub subset — fixtures isolate plugin
state under a per-test ``tmp_path`` so nothing leaks across runs.
"""

from __future__ import annotations

from pathlib import Path

import pytest


@pytest.fixture
def state_dir(tmp_path: Path) -> str:
    """Isolated plugin state directory for tests that instantiate stores."""
    return str(tmp_path / "baselithbot_state")

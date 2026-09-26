"""``BASELITH_LOG_DIR`` left blank means the default ``logs/`` directory.

The shipped ``.env.example`` carries ``BASELITH_LOG_DIR=`` and documents
"empty means logs/". The setup read the variable with a ``"logs"`` default that
only applies when the key is *absent*, so a blank value made
``os.makedirs("")`` fail and the file sink land in the working directory.
"""

from __future__ import annotations

import logging
from pathlib import Path
from unittest.mock import patch

import pytest

from core.observability.setup import ensure_logging_configured

pytestmark = [pytest.mark.unit]


@pytest.fixture
def isolated_root(monkeypatch, tmp_path):
    """Run the setup against a root logger with no file handler, then restore."""
    monkeypatch.chdir(tmp_path)
    root = logging.getLogger()
    saved_handlers = list(root.handlers)
    saved_level = root.level
    saved_filters = {h: list(h.filters) for h in saved_handlers}
    root.handlers = [
        h for h in saved_handlers if not isinstance(h, logging.FileHandler)
    ]
    yield tmp_path
    for handler in root.handlers:
        if handler not in saved_handlers:
            handler.close()
    for handler, filters in saved_filters.items():
        handler.filters = filters
    root.handlers = saved_handlers
    root.setLevel(saved_level)


@pytest.mark.parametrize("value", ["", "   "])
def test_blank_value_uses_the_default_directory(
    isolated_root: Path, monkeypatch, value: str
) -> None:
    monkeypatch.setenv("BASELITH_LOG_DIR", value)
    with patch("core.observability.logging.configure_logging"):
        ensure_logging_configured()

    assert (isolated_root / "logs" / "app.log").exists()
    assert not (isolated_root / "app.log").exists()


def test_explicit_directory_is_honoured(isolated_root: Path, monkeypatch) -> None:
    target = isolated_root / "custom"
    monkeypatch.setenv("BASELITH_LOG_DIR", str(target))
    with patch("core.observability.logging.configure_logging"):
        ensure_logging_configured()

    assert (target / "app.log").exists()

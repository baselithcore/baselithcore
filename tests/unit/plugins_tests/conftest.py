"""Shared fixtures for the plugin test suites."""

from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _restore_cli_dispatch_map():
    """Undo plugin CLI registration on the process-wide dispatch map.

    A plugin's ``register_parser`` injects its handler straight into
    ``core.cli.__main__.COMMAND_HANDLERS_MAP`` — that is how a plugin gets a
    top-level command, and it is permanent for the process. A test that
    registers a parser therefore left its command in the map for every later
    test, which is how the CLI tenant-scope coverage test failed under one
    random order and passed under another.
    """
    from core.cli.__main__ import COMMAND_HANDLERS_MAP

    snapshot = dict(COMMAND_HANDLERS_MAP)
    yield
    COMMAND_HANDLERS_MAP.clear()
    COMMAND_HANDLERS_MAP.update(snapshot)

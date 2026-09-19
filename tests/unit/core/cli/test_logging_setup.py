"""Tests for the CLI's ownership of logging configuration.

``CoreConfig`` leaves logging to whoever owns the process. Nothing owned it in
the CLI, so structlog stayed unconfigured and every command printed library
DEBUG records ahead of its own output while ``LOG_LEVEL_CONSOLE`` was ignored.
See :func:`core.cli.__main__._configure_cli_logging`.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import patch

from core.cli.__main__ import _configure_cli_logging


class _Config:
    """Stand-in for ``AppConfig`` carrying only what the function reads."""

    def __init__(self, *, console: str, json: bool, explicit: set[str]) -> None:
        self.log_level_console = console
        self.log_json = json
        self.model_fields_set = explicit


def _call_with(config: _Config) -> dict[str, Any]:
    """Run the setup against ``config``, returning the kwargs it passed on."""
    with (
        patch("core.config.get_app_config", return_value=config),
        patch("core.observability.logging.configure_logging") as configure,
    ):
        _configure_cli_logging()
    assert configure.call_count == 1
    return dict(configure.call_args.kwargs)


def test_quiet_by_default() -> None:
    """Nobody asked for a level: the CLI's stdout is UI, so WARNING."""
    kwargs = _call_with(_Config(console="INFO", json=True, explicit=set()))
    assert kwargs == {"level": "WARNING", "json_output": False}


def test_explicit_level_is_honoured() -> None:
    """An operator who names a level gets exactly that one, INFO included."""
    kwargs = _call_with(
        _Config(console="INFO", json=True, explicit={"log_level_console"})
    )
    assert kwargs["level"] == "INFO"


def test_explicit_debug_is_honoured() -> None:
    kwargs = _call_with(
        _Config(console="DEBUG", json=True, explicit={"log_level_console"})
    )
    assert kwargs["level"] == "DEBUG"


def test_json_only_when_asked_for() -> None:
    """``log_json`` defaults to true for servers; a terminal is not one."""
    assert (
        _call_with(_Config(console="INFO", json=True, explicit=set()))["json_output"]
        is False
    )
    assert (
        _call_with(_Config(console="INFO", json=True, explicit={"log_json"}))[
            "json_output"
        ]
        is True
    )


def test_a_broken_setup_does_not_break_the_command() -> None:
    """The CLI must still run; the only cost is the noise left in place."""
    with (
        patch("core.config.get_app_config", side_effect=RuntimeError("no config")),
        patch("core.cli.__main__._debug_log") as debug_log,
    ):
        _configure_cli_logging()
    assert debug_log.call_count == 1
    assert "failed to configure logging" in debug_log.call_args.args[0]

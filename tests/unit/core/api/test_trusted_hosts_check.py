"""Production must surface an unvalidated Host-header perimeter.

TrustedHostMiddleware is mounted only when TRUSTED_HOSTS is non-empty, and the
default is empty — so by default nothing validates Host/X-Forwarded-Host. The
startup check logs that at ERROR in production so alerting can act on it.

The module logger is asserted on directly rather than via ``caplog``: structlog
owns its own rendering pipeline here, so records do not reach caplog's handler.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

from core.api.startup_checks import _warn_missing_trusted_hosts


def _run(monkeypatch, *, production: bool, trusted: list[str]) -> MagicMock:
    """Run the check with a stubbed config/env and return the mocked logger."""
    monkeypatch.setattr("core.api.startup_checks.is_production_env", lambda: production)
    monkeypatch.setattr(
        "core.config.get_security_config",
        lambda: SimpleNamespace(trusted_hosts=trusted),
    )
    mock_logger = MagicMock()
    monkeypatch.setattr("core.api.startup_checks.logger", mock_logger)
    _warn_missing_trusted_hosts()
    return mock_logger


def test_errors_in_production_when_trusted_hosts_empty(monkeypatch) -> None:
    logger = _run(monkeypatch, production=True, trusted=[])
    logger.error.assert_called_once()
    assert "TRUSTED_HOSTS is empty" in logger.error.call_args[0][0]


def test_silent_in_production_when_configured(monkeypatch) -> None:
    logger = _run(monkeypatch, production=True, trusted=["api.example.com"])
    logger.error.assert_not_called()


def test_silent_outside_production(monkeypatch) -> None:
    """Local development routinely runs without TRUSTED_HOSTS; only the
    production perimeter is worth an alert."""
    logger = _run(monkeypatch, production=False, trusted=[])
    logger.error.assert_not_called()

"""``_warn_missing_trusted_hosts``: fail-closed in production.

``TrustedHostMiddleware`` is mounted only when ``TRUSTED_HOSTS`` is non-empty,
so an empty value in production leaves the ``Host`` header unvalidated. Like
the JWT trust perimeter, production refuses to boot unless the operator opts
out explicitly; outside production the check is silent.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from core.api.startup_checks import (
    UnvalidatedHostConfigError,
    _warn_missing_trusted_hosts,
)


def _run(monkeypatch, *, production: bool, trusted: list[str]) -> MagicMock:
    """Invoke the check under a controlled environment; return the logger."""
    monkeypatch.setattr("core.api.startup_checks.is_production_env", lambda: production)
    monkeypatch.setattr(
        "core.config.get_security_config",
        lambda: SimpleNamespace(trusted_hosts=trusted),
    )
    mock_logger = MagicMock()
    monkeypatch.setattr("core.api.startup_checks.logger", mock_logger)
    _warn_missing_trusted_hosts()
    return mock_logger


def test_refuses_to_boot_in_production_when_trusted_hosts_empty(monkeypatch) -> None:
    monkeypatch.delenv("BASELITH_ALLOW_UNVALIDATED_HOST", raising=False)
    with pytest.raises(UnvalidatedHostConfigError, match="TRUSTED_HOSTS"):
        _run(monkeypatch, production=True, trusted=[])


def test_explicit_optout_downgrades_to_error_log(monkeypatch) -> None:
    monkeypatch.setenv("BASELITH_ALLOW_UNVALIDATED_HOST", "true")
    logger = _run(monkeypatch, production=True, trusted=[])
    logger.error.assert_called_once()
    assert "TRUSTED_HOSTS" in logger.error.call_args.args[1]


def test_silent_in_production_when_configured(monkeypatch) -> None:
    monkeypatch.delenv("BASELITH_ALLOW_UNVALIDATED_HOST", raising=False)
    logger = _run(monkeypatch, production=True, trusted=["api.example.com"])
    logger.error.assert_not_called()
    logger.warning.assert_not_called()


def test_silent_outside_production(monkeypatch) -> None:
    monkeypatch.delenv("BASELITH_ALLOW_UNVALIDATED_HOST", raising=False)
    logger = _run(monkeypatch, production=False, trusted=[])
    logger.error.assert_not_called()
    logger.warning.assert_not_called()

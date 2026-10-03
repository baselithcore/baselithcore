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


def test_warns_in_production_when_only_loopback_hosts(monkeypatch) -> None:
    """The template's loopback allowlist carried into production is a 400 trap."""
    monkeypatch.delenv("BASELITH_ALLOW_UNVALIDATED_HOST", raising=False)
    logger = _run(monkeypatch, production=True, trusted=["localhost", "127.0.0.1"])
    logger.warning.assert_called_once()
    assert "loopback" in logger.warning.call_args.args[0]


def test_loopback_hosts_silent_outside_production(monkeypatch) -> None:
    logger = _run(monkeypatch, production=False, trusted=["localhost", "127.0.0.1"])
    logger.warning.assert_not_called()


def _template_trusted_hosts() -> list[str]:
    """The ``TRUSTED_HOSTS`` value ``.env.example`` ships, parsed as the app does."""
    from pathlib import Path

    from core.config._collections import csv_list

    template = Path(__file__).resolve().parents[4] / ".env.example"
    for line in template.read_text(encoding="utf-8").splitlines():
        if line.startswith("TRUSTED_HOSTS="):
            parsed = csv_list(line.partition("=")[2].strip())
            assert isinstance(parsed, list)
            return [str(host) for host in parsed]
    raise AssertionError("TRUSTED_HOSTS missing from .env.example")


@pytest.mark.parametrize(
    "host", ["localhost:8000", "127.0.0.1:8000", "localhost", "127.0.0.1"]
)
def test_template_trusted_hosts_admit_local_requests(host: str) -> None:
    """``cp .env.example .env`` must not answer every local request with 400."""
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from starlette.middleware.trustedhost import TrustedHostMiddleware

    app = FastAPI()

    @app.get("/ping")
    def _ping() -> dict[str, bool]:
        return {"ok": True}

    app.add_middleware(TrustedHostMiddleware, allowed_hosts=_template_trusted_hosts())
    response = TestClient(app).get("/ping", headers={"Host": host})
    assert response.status_code == 200


def test_template_trusted_hosts_still_reject_foreign_hosts() -> None:
    """Shipping a non-empty allowlist keeps Host validation on — not ``*``."""
    hosts = _template_trusted_hosts()
    assert hosts and "*" not in hosts
    assert "evil.example" not in hosts

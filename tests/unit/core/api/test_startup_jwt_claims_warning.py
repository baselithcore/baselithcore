"""Production enforcement for JWTs not bound to a deployment (missing aud/iss)."""

from unittest.mock import MagicMock, patch

import pytest

from core.api.startup_checks import UnboundJWTConfigError, _warn_unbound_jwt_claims


def _config(issuer, audience, auth_required=False):
    config = MagicMock()
    config.jwt_issuer = issuer
    config.jwt_audience = audience
    config.auth_required = auth_required
    return config


def test_refuses_startup_in_production_with_auth_and_both_unset():
    """Auth enabled + unbound tokens + production = fail closed at boot."""
    with (
        patch("core.api.startup_checks.is_production_env", return_value=True),
        patch(
            "core.config.get_security_config",
            return_value=_config(None, None, auth_required=True),
        ),
        patch.dict("os.environ", {}, clear=False),
    ):
        with pytest.raises(UnboundJWTConfigError, match="JWT_ISSUER and JWT_AUDIENCE"):
            _warn_unbound_jwt_claims()


def test_explicit_escape_hatch_downgrades_to_warning():
    """BASELITH_ALLOW_UNBOUND_JWT=true accepts the risk explicitly."""
    with (
        patch("core.api.startup_checks.is_production_env", return_value=True),
        patch(
            "core.config.get_security_config",
            return_value=_config(None, None, auth_required=True),
        ),
        patch.dict("os.environ", {"BASELITH_ALLOW_UNBOUND_JWT": "true"}),
        patch("core.api.startup_checks.logger") as log,
    ):
        _warn_unbound_jwt_claims()
    log.warning.assert_called_once()


def test_warns_only_when_auth_disabled():
    """Without auth there is no token perimeter to protect — warn, don't block."""
    with (
        patch("core.api.startup_checks.is_production_env", return_value=True),
        patch(
            "core.config.get_security_config",
            return_value=_config(None, None, auth_required=False),
        ),
        patch("core.api.startup_checks.logger") as log,
    ):
        _warn_unbound_jwt_claims()
    log.warning.assert_called_once()
    assert "JWT_ISSUER and JWT_AUDIENCE" in log.warning.call_args.args[1]


def test_silent_when_configured():
    with (
        patch("core.api.startup_checks.is_production_env", return_value=True),
        patch(
            "core.config.get_security_config",
            return_value=_config("https://issuer", "baselith-api", auth_required=True),
        ),
        patch("core.api.startup_checks.logger") as log,
    ):
        _warn_unbound_jwt_claims()
    log.warning.assert_not_called()


def test_silent_outside_production():
    with (
        patch("core.api.startup_checks.is_production_env", return_value=False),
        patch("core.config.get_security_config", return_value=_config(None, None)),
        patch("core.api.startup_checks.logger") as log,
    ):
        _warn_unbound_jwt_claims()
    log.warning.assert_not_called()

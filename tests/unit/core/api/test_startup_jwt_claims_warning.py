"""Production warning for JWTs not bound to a deployment (missing aud/iss)."""

from unittest.mock import MagicMock, patch

from core.api.startup_checks import _warn_unbound_jwt_claims


def _config(issuer, audience):
    config = MagicMock()
    config.jwt_issuer = issuer
    config.jwt_audience = audience
    return config


def test_warns_in_production_when_both_unset():
    with (
        patch("core.api.startup_checks.is_production_env", return_value=True),
        patch("core.config.get_security_config", return_value=_config(None, None)),
        patch("core.api.startup_checks.logger") as log,
    ):
        _warn_unbound_jwt_claims()
    log.warning.assert_called_once()
    assert "JWT_ISSUER and JWT_AUDIENCE" in log.warning.call_args.args[0] % (
        log.warning.call_args.args[1],
    )


def test_silent_when_configured():
    with (
        patch("core.api.startup_checks.is_production_env", return_value=True),
        patch(
            "core.config.get_security_config",
            return_value=_config("https://issuer", "baselith-api"),
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

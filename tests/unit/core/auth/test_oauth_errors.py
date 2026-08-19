"""RFC 6749 §5.2 error shape."""

from __future__ import annotations

from core.auth.oauth import InvalidClientError, InvalidGrantError, InvalidScopeError


def test_invalid_grant_serializes_to_rfc_shape() -> None:
    err = InvalidGrantError("authorization code already used")
    assert err.to_dict() == {
        "error": "invalid_grant",
        "error_description": "authorization code already used",
    }
    assert err.status_code == 400


def test_invalid_client_uses_401() -> None:
    assert InvalidClientError("unknown client").status_code == 401


def test_invalid_scope_reports_its_code() -> None:
    assert InvalidScopeError("no overlap").error == "invalid_scope"

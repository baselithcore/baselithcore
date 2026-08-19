"""PKCE verification: S256 only, constant-time comparison, no plain fallback."""

from __future__ import annotations

import pytest

from core.auth.oauth import (
    InvalidRequestError,
    derive_code_challenge,
    verify_code_challenge,
)

VERIFIER = "dBjftJeZ4CVP-mB92K27uhbUJU1p1r_wW1gFWFOEjXk"


def test_matching_verifier_is_accepted() -> None:
    challenge = derive_code_challenge(VERIFIER)
    assert verify_code_challenge(VERIFIER, challenge, "S256") is True


def test_derive_matches_rfc7636_appendix_b() -> None:
    # RFC 7636 Appendix B fixed vector.
    assert (
        derive_code_challenge(VERIFIER) == "E9Melhoa2OwvFrEMTJguCHaoeK1t8URWbuGJSstw-cM"
    )


def test_wrong_verifier_is_rejected() -> None:
    challenge = derive_code_challenge(VERIFIER)
    assert verify_code_challenge("not-the-verifier", challenge, "S256") is False


def test_plain_method_is_refused() -> None:
    with pytest.raises(InvalidRequestError) as exc:
        verify_code_challenge(VERIFIER, VERIFIER, "plain")
    assert exc.value.error == "invalid_request"


def test_unknown_method_is_refused() -> None:
    with pytest.raises(InvalidRequestError):
        verify_code_challenge(VERIFIER, "whatever", "S512")


def test_challenge_is_unpadded_base64url() -> None:
    challenge = derive_code_challenge(VERIFIER)
    assert "=" not in challenge and "+" not in challenge and "/" not in challenge

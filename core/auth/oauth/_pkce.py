"""PKCE (RFC 7636) challenge derivation and verification.

Only ``S256`` is supported. The ``plain`` method offers no protection against an
attacker who can read the authorization request, and OAuth 2.1 removes it; this
module refuses it explicitly rather than silently downgrading.
"""

from __future__ import annotations

import base64
import hashlib
import hmac

from core.auth.oauth._errors import InvalidRequestError

#: The only accepted ``code_challenge_method``.
S256 = "S256"


def derive_code_challenge(verifier: str) -> str:
    """Derive the ``code_challenge`` for a verifier.

    Args:
        verifier: The client's ``code_verifier``.

    Returns:
        Unpadded base64url encoding of ``SHA-256(verifier)``.
    """
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    return base64.urlsafe_b64encode(digest).decode("ascii").rstrip("=")


def verify_code_challenge(verifier: str, challenge: str, method: str) -> bool:
    """Check a ``code_verifier`` against the stored ``code_challenge``.

    Args:
        verifier: The verifier presented at the token endpoint.
        challenge: The challenge recorded at the authorization endpoint.
        method: The recorded ``code_challenge_method``.

    Returns:
        True when the verifier matches.

    Raises:
        InvalidRequestError: If ``method`` is anything other than ``S256``.
    """
    if method != S256:
        raise InvalidRequestError(
            f"code_challenge_method {method!r} is not supported; use S256"
        )
    # Constant-time: the challenge is attacker-influenced input compared against
    # a value derived from a secret the attacker is trying to guess.
    return hmac.compare_digest(derive_code_challenge(verifier), challenge)

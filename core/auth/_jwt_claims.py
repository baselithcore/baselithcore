"""Reserved JWT claims and extra-claims sanitization.

Split out of ``core.auth.jwt`` to keep that module under the file-size cap;
the semantics are unchanged and ``jwt.py`` re-exports both names.
"""

from __future__ import annotations

from typing import Any

from core.observability.logging import get_logger

logger = get_logger(__name__)

# Claims that carry security meaning and must be derived from the handler's own
# parameters, never from caller-supplied ``extra_claims``. Without this guard an
# ``extra_claims`` dict (potentially built from user-influenced data) could
# override ``roles``/``exp``/``type``/``sub`` after they were set — minting a
# token with elevated privileges, an extended lifetime, or a forged token type.
# ``tenant_id`` IS reserved and threaded as a first-class ``create_token`` /
# ``create_refresh_token`` parameter: it is the multi-tenant isolation boundary,
# so leaving it caller-overridable via ``extra_claims`` would let any path that
# folds user-influenced data into ``extra_claims`` mint a token asserting an
# arbitrary tenant. ``family`` IS reserved: it chains a refresh token to its
# rotation lineage for theft detection, so a caller-supplied value could graft a
# token onto (or detach it from) another lineage.
_RESERVED_CLAIMS = frozenset(
    {
        "sub",
        "exp",
        "iat",
        "nbf",
        "jti",
        "iss",
        "aud",
        "roles",
        "scopes",
        "type",
        "family",
        "tenant_id",
        "tv",
    }
)


def _sanitize_extra_claims(
    extra_claims: dict[str, Any] | None,
) -> dict[str, Any] | None:
    """Drop reserved (security-bearing) keys from caller-supplied extra claims."""
    if not extra_claims:
        return extra_claims
    safe = {k: v for k, v in extra_claims.items() if k not in _RESERVED_CLAIMS}
    dropped = extra_claims.keys() - safe.keys()
    if dropped:
        logger.warning(
            "jwt_extra_claims_reserved_keys_dropped",
            dropped=sorted(dropped),
        )
    return safe

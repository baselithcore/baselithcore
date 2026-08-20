"""JWT signing/verification key material, and rotating it without downtime.

A single shared secret used for both signing and verification has two costs
that only show up once you try to operate the system:

- **Every service that can verify a token can also mint one.** With HMAC there
  is no way to hand a downstream service the ability to check a token without
  also handing it the ability to forge one.
- **Rotating the key invalidates every live session at once**, because there is
  a single accepted key and it just changed. The practical consequence is that
  the key never gets rotated at all.

A key *ring* fixes both. Verification accepts any key in the ring, selected by
the token's ``kid`` header, while signing uses exactly one — so a rotation is
"add the new key, switch signing to it, drop the old one once the longest token
lifetime has passed", with no session loss at any step. Asymmetric algorithms
additionally let the ring hold only public keys on services that verify.

Nothing here changes the default: with no ring configured, the deployment keeps
using its single ``SECRET_KEY`` with HS256 and unlabelled tokens.
"""

from __future__ import annotations

from typing import Any

import jwt
from jwt.algorithms import requires_cryptography
from pydantic import SecretStr

from core.observability.logging import get_logger

logger = get_logger(__name__)

# Algorithms that disable signature verification. Accepting one turns every
# token into an unauthenticated assertion, so they are refused at construction
# rather than merely discouraged. The empty string is included because PyJWT
# treats it as unset and would fall back to its own default — a silent choice
# where the operator clearly meant to make an explicit one.
FORBIDDEN_ALGORITHMS = frozenset({"none", ""})


def _unwrap(value: str | SecretStr) -> str:
    return value.get_secret_value() if isinstance(value, SecretStr) else value


class JWTKeyRing:
    """The keys a :class:`~core.auth.jwt.JWTHandler` signs and verifies with.

    Args:
        secret_key: The deployment secret. Signs and verifies when no ring is
            configured, and remains a verification fallback when one is.
        algorithm: JWS algorithm. HMAC (``HS*``) uses the secret directly;
            asymmetric algorithms (``RS*``/``ES*``/``EdDSA``) sign with
            ``signing_key`` and verify with the public keys in ``keys``.
        keys: Verification key set as ``{kid: key material}``. For HMAC these
            are shared secrets; for asymmetric algorithms, public keys in PEM.
        active_kid: Which entry of ``keys`` signs new tokens. Its ``kid`` goes
            in the token header so a verifier knows which key to reach for.
        signing_key: Private key for asymmetric signing. Omit on a service that
            should only be able to *verify* — the ring then refuses to sign,
            which is the point of using an asymmetric algorithm at all.
    """

    def __init__(
        self,
        secret_key: str | SecretStr,
        algorithm: str = "HS256",
        keys: dict[str, str | SecretStr] | None = None,
        active_kid: str | None = None,
        signing_key: str | SecretStr | None = None,
    ) -> None:
        if algorithm.strip().lower() in {a.lower() for a in FORBIDDEN_ALGORITHMS}:
            raise ValueError(
                f"JWT algorithm {algorithm!r} is not allowed: it disables "
                "signature verification. Use HS256/RS256/ES256/EdDSA."
            )
        self.algorithm = algorithm
        self._secret = _unwrap(secret_key)
        self._keys = {kid: _unwrap(key) for kid, key in (keys or {}).items()}
        self._signing_key_raw = _unwrap(signing_key) if signing_key else None

        if active_kid and active_kid not in self._keys:
            raise ValueError(
                f"JWT_ACTIVE_KID={active_kid!r} is not present in JWT_KEYS "
                f"(known: {sorted(self._keys) or 'none'})"
            )
        self.active_kid = active_kid or (
            # A ring with exactly one key needs no explicit choice — anything
            # else is ambiguous and must be stated, or a rotation would silently
            # keep signing with whichever key happened to be enumerated first.
            next(iter(self._keys)) if len(self._keys) == 1 else None
        )
        if self._keys and self.active_kid is None:
            raise ValueError(
                "JWT_KEYS lists several keys but JWT_ACTIVE_KID does not say "
                "which one signs. Set it to the kid that should sign new tokens."
            )

        self._prepared: dict[str, Any] = {}

    @property
    def is_asymmetric(self) -> bool:
        return self.algorithm in requires_cryptography

    @property
    def signing_key(self) -> str:
        """The key new tokens are signed with.

        Raises:
            RuntimeError: On a verify-only deployment (asymmetric algorithm with
                no private key configured). Failing loudly beats silently
                falling back to the shared secret, which would produce tokens
                nobody in the fleet can verify.
        """
        if self._signing_key_raw:
            return self._signing_key_raw
        if self.is_asymmetric:
            raise RuntimeError(
                f"JWT_ALGORITHM={self.algorithm} requires JWT_SIGNING_KEY (the "
                "private key). Without it this process can verify tokens but "
                "not mint them."
            )
        if self.active_kid:
            return self._keys[self.active_kid]
        return self._secret

    def verification_key(self, kid: str | None) -> Any:
        """The key to verify a token bearing ``kid``.

        With a ring configured, an unknown ``kid`` is a hard reject: after a
        rotation drops a key, tokens still naming it would otherwise be
        silently adjudicated against the active key or the deployment secret —
        degrading the ring's isolation to the single-secret model without a
        signal. On a ringless deployment the label is meaningless (a peer
        upgraded mid-rollout may already emit one) and verification falls back
        to the only key there is, the deployment secret.

        Raises:
            jwt.InvalidTokenError: When ``kid`` names no key in a configured
                ring.
        """
        raw = self._keys.get(kid) if kid else None
        if raw is None:
            if kid and self._keys:
                raise jwt.InvalidTokenError(f"Unknown JWT key id {kid!r}")
            raw = self._secret
        return self._prepare(raw)

    def candidate_keys(self) -> list[Any]:
        """Every key a token might legitimately be verified against.

        Used for tokens carrying no ``kid`` — those minted before the ring was
        introduced, or by a peer still running the old configuration. Trying
        each is what makes a rotation seamless for tokens already in flight.
        """
        raws = list(self._keys.values()) or []
        # The HMAC deployment secret is only a legitimate candidate under HMAC:
        # under RS*/ES*/EdDSA PyJWT raises InvalidKeyError for it — which is
        # not an InvalidTokenError and would surface as a 500 instead of a 401.
        if not self.is_asymmetric and self._secret not in raws:
            raws.append(self._secret)
        return [self._prepare(raw) for raw in raws]

    def encode(self, payload: dict[str, Any]) -> str:
        """Sign a payload with the active key, labelling it with that key's id.

        The ``kid`` header is what makes rotation non-disruptive: a verifier
        reads it to pick the right key out of the ring instead of having to
        guess. Omitted when no ring is configured, so tokens stay byte-for-byte
        what a single-secret deployment produced before.
        """
        headers = {"kid": self.active_kid} if self.active_kid else None
        return jwt.encode(
            payload,
            self.signing_key,
            algorithm=self.algorithm,
            headers=headers,
        )

    def decode(self, token: str, **kwargs: Any) -> dict[str, Any]:
        """Verify a token against whichever ring key its ``kid`` names.

        A token with no ``kid`` — minted before the ring existed, or by a peer
        still on the old config — is tried against every key in the ring. That
        is what lets a rotation proceed while tokens signed by the previous key
        are still in flight; each attempt is a full signature verification, so
        trying several costs latency, never safety.
        """
        kid = None
        try:
            kid = jwt.get_unverified_header(token).get("kid")
        except jwt.InvalidTokenError:
            # Malformed header — let the real decode below produce the error, so
            # the caller sees one consistent exception type.
            pass

        if kid:
            try:
                return jwt.decode(
                    token,
                    self.verification_key(kid),
                    algorithms=[self.algorithm],
                    **kwargs,
                )
            except jwt.InvalidKeyError as exc:
                # Key material incompatible with the pinned algorithm (e.g. an
                # HMAC secret reached an RS256 verification). A malformed key
                # is a verification failure, not a server fault — callers
                # handle InvalidTokenError and turn it into a 401, while
                # InvalidKeyError would escape them as a 500.
                raise jwt.InvalidTokenError(
                    "Token could not be verified with the configured key"
                ) from exc

        last_error: jwt.PyJWTError | None = None
        for key in self.candidate_keys():
            try:
                return jwt.decode(token, key, algorithms=[self.algorithm], **kwargs)
            except jwt.ExpiredSignatureError:
                # Expiry is a property of the token, not of the key — trying
                # another key cannot change the verdict, and swallowing it here
                # would report a valid-but-expired token as an invalid one.
                raise
            except (jwt.InvalidTokenError, jwt.InvalidKeyError) as exc:
                last_error = exc
        if isinstance(last_error, jwt.InvalidTokenError):
            raise last_error
        raise jwt.InvalidTokenError("Token could not be verified") from last_error

    def _prepare(self, raw: str) -> Any:
        """Parse a key once and reuse it.

        PyJWT re-parses PEM material on every decode; for asymmetric algorithms
        that is the dominant cost of verifying a token.
        """
        cached = self._prepared.get(raw)
        if cached is not None:
            return cached
        prepared: Any = raw
        if self.is_asymmetric:
            try:
                prepared = jwt.get_algorithm_by_name(self.algorithm).prepare_key(raw)
            except Exception:  # pragma: no cover - defensive
                # Correctness is preserved by per-call parsing; only the
                # optimisation is lost. Happens when the configured key is in
                # the private (signing) form on a verifying path.
                logger.warning(
                    "jwt_verify_key_preparse_failed", algorithm=self.algorithm
                )
                prepared = raw
        self._prepared[raw] = prepared
        return prepared


def parse_key_map(raw: str | None) -> dict[str, str]:
    """Parse ``kid1=key1,kid2=key2`` into a mapping.

    PEM keys contain no commas but plenty of newlines, so entries are split on
    commas and the key material is taken verbatim after the first ``=``. Blank
    and malformed entries are skipped with a warning rather than aborting
    startup on one typo in a multi-key ring.
    """
    if not raw:
        return {}
    out: dict[str, str] = {}
    for entry in raw.split(","):
        entry = entry.strip()
        if not entry:
            continue
        kid, sep, material = entry.partition("=")
        if not sep or not kid.strip() or not material.strip():
            logger.warning("jwt_keys_entry_malformed", entry=kid[:16])
            continue
        out[kid.strip()] = material.strip().replace("\\n", "\n")
    return out


__all__ = ["FORBIDDEN_ALGORITHMS", "JWTKeyRing", "parse_key_map"]

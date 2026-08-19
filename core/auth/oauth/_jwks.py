"""JWKS document construction from PEM public keys.

The signing key ring holds PEM; verifiers want JWK. PyJWT's algorithm registry
already knows how to make that conversion, so this module is a thin, auditable
adapter whose real job is guaranteeing the private half never appears in the
output.
"""

from __future__ import annotations

import json
from collections.abc import Mapping

from cryptography.hazmat.primitives.asymmetric.ec import EllipticCurvePublicKey
from cryptography.hazmat.primitives.serialization import load_pem_public_key
from jwt.algorithms import ECAlgorithm

#: Only ES256 keys are published; the deployment's signing algorithm is fixed.
_ALG = "ES256"

#: JWK members that would leak private key material if ever emitted.
_PRIVATE_MEMBERS = frozenset({"d", "p", "q", "dp", "dq", "qi"})


def build_jwks_document(
    public_keys: Mapping[str, str],
) -> dict[str, list[dict[str, str]]]:
    """Convert ``{kid: PEM public key}`` into a JWKS document.

    Args:
        public_keys: Public keys in PEM form, keyed by ``kid``.

    Returns:
        A JWKS document with one entry per key.

    Raises:
        ValueError: If a key is not an EC public key, or if it serializes
            with private members present, either of which would mean the
            wrong key material was passed in by mistake.
    """
    keys: list[dict[str, str]] = []
    for kid, pem in public_keys.items():
        key_obj = load_pem_public_key(pem.encode())
        if not isinstance(key_obj, EllipticCurvePublicKey):
            raise ValueError(
                f"key {kid!r} is not an EC public key; only ES256 is supported"
            )
        jwk = json.loads(ECAlgorithm.to_jwk(key_obj))
        leaked = _PRIVATE_MEMBERS & set(jwk)
        if leaked:
            raise ValueError(
                f"refusing to publish key {kid!r}: private members present "
                f"({', '.join(sorted(leaked))})"
            )
        jwk.update({"kid": kid, "alg": _ALG, "use": "sig"})
        keys.append(jwk)
    return {"keys": keys}

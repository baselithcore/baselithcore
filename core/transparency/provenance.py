"""
Provenance tagging for AI-generated content (EU AI Act Article 50(2)/(4)).

A :class:`ProvenanceTagger` turns a piece of model output into a machine-readable
:class:`~core.transparency.types.ProvenanceTag` that records *what produced it*
and a SHA-256 of the exact bytes. When a signing secret is supplied the tag is
additionally HMAC-SHA256 signed (same primitive family as the webhook signer) so
a verifier can detect tampering or forged provenance.

The header name is fixed so deployers attach provenance consistently::

    X-Baselith-AI-Provenance: <base64url(json)>

This is a *labeling* mechanism, which is what Art 50(2) mandates ("marked in a
machine-readable format and detectable as artificially generated"). Statistical
watermarking (e.g. SynthID) operates at model-decoding time and is the model
provider's responsibility — it cannot be added downstream of the logits and is
out of scope here by design.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json

from pydantic import SecretStr

from core.transparency.types import (
    ContentClass,
    Modality,
    ProvenanceTag,
    TransparencyError,
)

PROVENANCE_HEADER = "X-Baselith-AI-Provenance"


def sha256_hex(content: str | bytes) -> str:
    """Hex SHA-256 of ``content`` (UTF-8 encoded if given as ``str``)."""
    data = content.encode("utf-8") if isinstance(content, str) else content
    return hashlib.sha256(data).hexdigest()


def _canonical(payload: dict) -> bytes:
    """Deterministic JSON for signing — sorted keys, no insignificant whitespace."""
    return json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")


class ProvenanceTagger:
    """Builds (and optionally signs/verifies) provenance tags for content."""

    def __init__(
        self,
        claim_generator: str,
        *,
        signing_secret: SecretStr | None = None,
    ) -> None:
        self._claim_generator = claim_generator
        self._signing_secret = signing_secret

    def _sign(self, tag: ProvenanceTag) -> str | None:
        if self._signing_secret is None:
            return None
        secret = self._signing_secret.get_secret_value().encode("utf-8")
        return hmac.new(
            secret, _canonical(tag.signable_payload()), hashlib.sha256
        ).hexdigest()

    def mark(
        self,
        content: str | bytes,
        *,
        content_class: ContentClass = ContentClass.AI_GENERATED,
        modality: Modality = Modality.TEXT,
        model: str | None = None,
    ) -> ProvenanceTag:
        """Create a provenance tag for ``content``.

        Args:
            content: The exact output bytes/text being marked.
            content_class: Whether the content is AI-generated or AI-modified.
            modality: Text/image/audio/video.
            model: Identifier of the model that produced the content.

        Returns:
            A :class:`ProvenanceTag`, signed when the tagger holds a secret.
        """
        tag = ProvenanceTag(
            content_class=content_class,
            modality=modality,
            claim_generator=self._claim_generator,
            model=model,
            content_sha256=sha256_hex(content),
        )
        tag.signature = self._sign(tag)
        return tag

    def verify(self, tag: ProvenanceTag, content: str | bytes) -> bool:
        """Verify a tag binds to ``content`` and (if signed) is authentic.

        Returns ``True`` only when the content hash matches and — if the tagger
        is configured with a secret — the HMAC signature is valid. A tag that
        carries no signature is rejected when a secret is configured (an
        unsigned tag cannot be trusted under a signing policy).
        """
        if sha256_hex(content) != tag.content_sha256:
            return False
        if self._signing_secret is None:
            return True
        if not tag.signature:
            return False
        expected = self._sign(tag)
        assert expected is not None  # secret is set, so _sign returns a value
        return hmac.compare_digest(expected, tag.signature)

    def to_header_value(self, tag: ProvenanceTag) -> str:
        """Encode a tag for the ``X-Baselith-AI-Provenance`` header (base64url JSON)."""
        raw = _canonical(tag.to_dict())
        return base64.urlsafe_b64encode(raw).decode("ascii")

    @staticmethod
    def from_header_value(value: str) -> ProvenanceTag:
        """Decode a header value back into a :class:`ProvenanceTag`."""
        try:
            raw = base64.urlsafe_b64decode(value.encode("ascii"))
            payload = json.loads(raw)
        except (ValueError, json.JSONDecodeError) as exc:
            raise TransparencyError("malformed provenance header") from exc
        return ProvenanceTag(
            content_class=ContentClass(payload["content_class"]),
            modality=Modality(payload.get("modality", "text")),
            claim_generator=payload["claim_generator"],
            model=payload.get("model"),
            created_at=payload["created_at"],
            content_sha256=payload["content_sha256"],
            signature=payload.get("signature"),
        )

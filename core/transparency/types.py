"""
Types for AI transparency (EU AI Act Article 50).

Two distinct obligations are modelled here:

* **Disclosure** — natural persons must be told they are interacting with an AI
  system (Art 50(1)). Represented by :class:`DisclosureNotice`.
* **Content marking** — synthetic or AI-manipulated output must carry a
  machine-readable marker that it was artificially generated or modified
  (Art 50(2)/(4)). Represented by :class:`ProvenanceTag`.

The tag fields are aligned with C2PA / Content Credentials semantics (a
``claim_generator`` plus a content hash and an action) so a downstream deployer
can promote a :class:`ProvenanceTag` into a full C2PA manifest for media without
reshaping the data model. Full cryptographic C2PA manifest embedding (JUMBF/COSE)
is intentionally out of scope for the core primitive — it requires media-format
tooling and belongs in a deployer/plugin layer.
"""

from __future__ import annotations

import time
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


class TransparencyError(Exception):
    """Base error for the transparency subsystem."""


class ContentClass(str, Enum):
    """How a piece of content relates to AI generation (Art 50(2)/(4))."""

    HUMAN = "human"  # authored by a natural person, no AI involvement
    AI_GENERATED = "ai_generated"  # produced by an AI system (synthetic)
    AI_MODIFIED = "ai_modified"  # human-origin content altered by an AI system


class Modality(str, Enum):
    """Content modality — drives which Art 50 paragraph applies downstream."""

    TEXT = "text"
    IMAGE = "image"
    AUDIO = "audio"
    VIDEO = "video"


class DisclosureNotice(BaseModel):
    """An AI-interaction disclosure shown/returned to an end user (Art 50(1))."""

    text: str
    provider: str | None = None
    # ``True`` marks this as machine-readable so clients can render it distinctly
    # rather than treat it as model output.
    machine_readable: bool = True
    version: str = "1"

    def to_dict(self) -> dict[str, Any]:
        return {
            "ai_disclosure": self.text,
            "provider": self.provider,
            "machine_readable": self.machine_readable,
            "version": self.version,
        }


class ProvenanceTag(BaseModel):
    """Machine-readable marker that content is AI-generated/-modified (Art 50(2))."""

    content_class: ContentClass
    modality: Modality = Modality.TEXT
    # Identifies the producing system, C2PA ``claim_generator`` style.
    claim_generator: str
    model: str | None = None
    created_at: float = Field(default_factory=time.time)
    # SHA-256 of the exact content bytes — binds the tag to the artefact and
    # lets a verifier detect post-hoc tampering.
    content_sha256: str
    # Optional HMAC-SHA256 over the canonical tag (hex). Present only when the
    # tagger was given a signing secret.
    signature: str | None = None

    @property
    def is_synthetic(self) -> bool:
        """Whether the content must be marked as artificially generated/modified."""
        return self.content_class in (
            ContentClass.AI_GENERATED,
            ContentClass.AI_MODIFIED,
        )

    def signable_payload(self) -> dict[str, Any]:
        """The deterministic subset of fields covered by the signature."""
        return {
            "content_class": self.content_class.value,
            "modality": self.modality.value,
            "claim_generator": self.claim_generator,
            "model": self.model,
            "created_at": self.created_at,
            "content_sha256": self.content_sha256,
        }

    def to_dict(self) -> dict[str, Any]:
        payload = self.signable_payload()
        payload["is_synthetic"] = self.is_synthetic
        payload["signature"] = self.signature
        return payload

    def c2pa_assertion(self) -> dict[str, Any]:
        """Render as a C2PA-aligned assertion bundle (for downstream manifests)."""
        action = {
            ContentClass.AI_GENERATED: "c2pa.created",
            ContentClass.AI_MODIFIED: "c2pa.edited",
            ContentClass.HUMAN: "c2pa.opened",
        }[self.content_class]
        return {
            "claim_generator": self.claim_generator,
            "assertions": [
                {
                    "label": "c2pa.actions",
                    "data": {
                        "actions": [
                            {
                                "action": action,
                                "softwareAgent": self.model or self.claim_generator,
                                "digitalSourceType": (
                                    "trainedAlgorithmicMedia"
                                    if self.is_synthetic
                                    else "humanEdits"
                                ),
                            }
                        ]
                    },
                },
                {
                    "label": "c2pa.hash.data",
                    "data": {"alg": "sha256", "hash": self.content_sha256},
                },
            ],
        }

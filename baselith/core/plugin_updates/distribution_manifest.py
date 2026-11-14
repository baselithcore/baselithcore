"""The signed manifest of a distribution release (``distribution-release.json``).

A release of the distribution (the core this tree ships, and in a downstream
distribution its own components) can be installed in place on a host only
through this manifest. It names exactly one commit (``commit_sha``, 40 hex),
the SHA-256 of that commit's ``uv.lock`` (``lock_sha256``) and how the
release's schema migrations behave (``migrations``: ``none``, ``expand`` or
``breaking``). A rollback never reverts the schema, so ``breaking`` keeps a
release out of one-click installs altogether. A release that ships a container
image also names its digest (``image_digest``, ``sha256:<64 hex>``): a GitOps
update writes exactly that value, so the image a cluster deploys is covered by
the same signature as the commit a host checks out.

The signature is the plugin release signature
(:func:`~.release_manifest.sign_release_manifest`, Ed25519 over the canonical
JSON of every other key, domain separated), and the strict loader
(:func:`~.release_manifest.load_release_json`) refuses duplicate keys and
floats. Unlike a plugin ``release.json`` it carries no ``files`` map (the
commit is the content), and ``kind: "distribution"`` keeps either manifest from
being accepted as the other. The caller passes the keys trusted for the
distribution's own trust scope (:func:`distribution_trust_scope`,
``distribution:<name>``, through ``_core_state.distribution_keys``): an
unscoped publisher key covers it, a key scoped to plugin names never does,
not even to a plugin that happens to share the distribution's name (plugin
names cannot contain ``:``).
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any, Final, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from .models import Refusal
from .release_manifest import (
    RELEASE_FORMAT,
    SIGNATURE_KEY,
    ReleaseManifestError,
    load_release_json,
    verify_release_manifest,
)

#: Name of the release asset that carries the signed manifest.
DISTRIBUTION_ASSET: Final = "distribution-release.json"
#: The ``kind`` of a distribution manifest (a plugin ``release.json`` has none).
DISTRIBUTION_KIND: Final = "distribution"
#: Name the public core signs its own releases under (no ``__distribution__``).
PUBLIC_CORE_NAME: Final = "baselithcore"
#: Prefix of a distribution's trust scope (plugin names never contain ``:``).
DISTRIBUTION_SCOPE_PREFIX: Final = "distribution:"
#: Pattern of a signed container image digest.
IMAGE_DIGEST_PATTERN: Final = r"^sha256:[0-9a-f]{64}$"

Migrations = Literal["none", "expand", "breaking"]

_ENVELOPE = frozenset({"release_format", "kind", SIGNATURE_KEY})


class DistributionManifest(BaseModel):
    """What a signed distribution release pins."""

    model_config = ConfigDict(frozen=True, strict=True, extra="forbid")

    name: str = Field(min_length=1, max_length=128)
    version: str = Field(min_length=1, max_length=64)
    commit_sha: str = Field(pattern=r"^[0-9a-f]{40}$")
    lock_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    migrations: Migrations
    min_core_version: str | None = None
    #: The release's container image digest, when it ships an image.
    image_digest: str | None = Field(default=None, pattern=IMAGE_DIGEST_PATTERN)


class DistributionRefused(ReleaseManifestError):
    """The manifest was refused; ``refusal`` says why (its value is the message)."""

    def __init__(self, refusal: Refusal, detail: str = "") -> None:
        super().__init__(refusal.value)
        self.refusal = refusal
        self.detail = detail


def distribution_trust_scope(name: str) -> str:
    """The trust-store ``plugins`` entry that scopes a key to ``name``'s releases."""
    return f"{DISTRIBUTION_SCOPE_PREFIX}{name}"


def build_manifest_meta(manifest: DistributionManifest) -> dict[str, Any]:
    """The unsigned JSON object of ``manifest``, envelope keys included."""
    return {
        "release_format": RELEASE_FORMAT,
        "kind": DISTRIBUTION_KIND,
        **manifest.model_dump(),
    }


def _refusal_detail(
    meta: Mapping[str, Any],
    trusted_keys: Sequence[str],
    expected_name: str,
    expected_version: str,
) -> tuple[Refusal, str] | DistributionManifest:
    if not trusted_keys:
        return Refusal.NO_TRUSTED_KEYS, ""
    if not verify_release_manifest(meta, trusted_keys):
        return Refusal.SIGNATURE_INVALID, "distribution manifest signature"
    fmt = meta.get("release_format")
    if type(fmt) is not int or fmt != RELEASE_FORMAT:
        return Refusal.MANIFEST_INVALID, "unsupported release_format"
    if meta.get("kind") != DISTRIBUTION_KIND:
        return Refusal.MANIFEST_INVALID, "not a distribution manifest"
    body = {k: v for k, v in meta.items() if k not in _ENVELOPE}
    try:
        manifest = DistributionManifest.model_validate(body)
    except ValidationError as exc:
        fields = sorted({str(e["loc"][0]) for e in exc.errors() if e["loc"]})
        return Refusal.MANIFEST_INVALID, f"invalid fields: {', '.join(fields)}"
    if manifest.name != expected_name:
        return Refusal.NAME_MISMATCH, f"signed for {manifest.name!r}"
    if manifest.version != expected_version:
        return Refusal.VERSION_MISMATCH, f"signed for {manifest.version!r}"
    return manifest


def distribution_refusal(
    meta: Mapping[str, Any],
    trusted_keys: Sequence[str],
    *,
    expected_name: str,
    expected_version: str,
) -> tuple[Refusal | None, DistributionManifest | None]:
    """``(None, manifest)`` for a valid manifest, else ``(refusal, None)``.

    Checked in order: a trusted key exists, the signature verifies against
    one, ``release_format`` is 2, ``kind`` is ``distribution``, every field
    is well formed (and no unknown one is present), then the name and the
    version are the ones expected.
    """
    found = _refusal_detail(meta, trusted_keys, expected_name, expected_version)
    if isinstance(found, DistributionManifest):
        return None, found
    return found[0], None


def load_distribution_manifest(
    raw: bytes,
    trusted_keys: Sequence[str],
    *,
    expected_name: str,
    expected_version: str,
) -> DistributionManifest:
    """Parse strictly and verify ``raw``.

    Raises:
        ReleaseManifestError: ``raw`` is not strictly well-formed JSON
            (duplicate key, float, not an object).
        DistributionRefused: The manifest was refused (``refusal`` says why).
    """
    meta = load_release_json(raw)
    found = _refusal_detail(meta, trusted_keys, expected_name, expected_version)
    if isinstance(found, DistributionManifest):
        return found
    raise DistributionRefused(*found)


__all__ = [
    "DISTRIBUTION_ASSET",
    "DISTRIBUTION_KIND",
    "DISTRIBUTION_SCOPE_PREFIX",
    "IMAGE_DIGEST_PATTERN",
    "PUBLIC_CORE_NAME",
    "DistributionManifest",
    "DistributionRefused",
    "Migrations",
    "build_manifest_meta",
    "distribution_trust_scope",
    "distribution_refusal",
    "load_distribution_manifest",
]

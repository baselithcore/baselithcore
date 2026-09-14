"""Write supply-chain fields back into a plugin manifest, in place.

Signing a plugin means stamping three keys into the very file that was hashed:
``integrity_sha256``, ``signature_ed25519`` and ``hash_surface_version``. Doing
that by round-tripping the YAML (``yaml.safe_load`` → ``yaml.safe_dump``) threw
away every comment in the file — and plugin manifests carry the *rationale* for
the permissions they declare, which is the part a reviewer reads. This module
rewrites the three lines and leaves everything else byte-identical.

Two callers share it so the delicate part exists once:
``scripts/sign_changed_plugins.py`` (the pre-commit hook and the on-demand
re-signer) and ``baselith plugin sign``. Each computes its own signature and
decides its own reporting; both write through :func:`set_manifest_fields`.

Imports are stdlib + PyYAML only, so the module can be direct-loaded by the
hook without dragging in the pydantic/structlog stack — the same constraint
``core/plugins/integrity.py`` documents.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any

#: The digest over the plugin's executable surface.
HASH_KEY = "integrity_sha256"
#: The publisher's Ed25519 signature over that digest.
SIGNATURE_KEY = "signature_ed25519"
#: Which generation of the hashed surface the digest was computed under.
SURFACE_KEY = "hash_surface_version"


def declared_hash(data: Mapping[str, Any]) -> str:
    """The digest a manifest currently claims, normalised for comparison.

    Coerced rather than type-checked: YAML hands back an ``int`` for an
    all-digit digest, and a manifest primed for its first signature carries a
    blank value (``None``). Both must compare unequal to a real digest without
    the caller having to know that.

    Args:
        data: The parsed manifest.

    Returns:
        The lowercase, stripped ``integrity_sha256`` value, or ``""``.
    """
    return str(data.get(HASH_KEY) or "").strip().lower()


def stale_signature_present(data: Mapping[str, Any], digest: str) -> bool:
    """Whether the manifest holds a signature that ``digest`` invalidates.

    The signature attests ``integrity_sha256`` as the manifest declared it. It
    only goes stale when the recomputed digest differs from that declared
    value — re-running a signing tool over an unchanged tree must leave a valid
    signature alone, or ``--all`` would strip the signature off every plugin in
    the repository.

    Args:
        data: The parsed manifest.
        digest: The freshly computed integrity hash.

    Returns:
        ``True`` when a non-empty signature is present *and* the digest moved.
    """
    if not str(data.get(SIGNATURE_KEY) or "").strip():
        return False
    return declared_hash(data) != digest.strip().lower()


def supply_chain_fields(
    data: Mapping[str, Any],
    digest: str,
    *,
    signature: str | None,
    surface_version: int,
) -> dict[str, Any]:
    """Work out which supply-chain fields actually need rewriting.

    Returning only the changed keys is what makes signing idempotent: a second
    run over an already-signed tree produces an empty mapping, so the file is
    not touched (and, in the pre-commit hook, not re-staged).

    Args:
        data: The parsed manifest as it stands on disk.
        digest: The freshly computed integrity hash.
        signature: The hex signature to write, ``""`` to blank an existing
            signature that no longer attests the declared hash, or ``None`` to
            leave the field exactly as it is.
        surface_version: The ``HashSurface`` generation ``digest`` was computed
            under.

    Returns:
        ``{key: value}`` for every supply-chain key whose stored value differs
        from what it should be. Possibly empty.
    """
    fields: dict[str, Any] = {}
    if declared_hash(data) != digest.strip().lower():
        fields[HASH_KEY] = digest
    if data.get(SURFACE_KEY) != surface_version:
        fields[SURFACE_KEY] = surface_version
    if signature is not None and data.get(SIGNATURE_KEY) != signature:
        fields[SIGNATURE_KEY] = signature
    return fields


def set_yaml_fields(text: str, fields: Mapping[str, str]) -> str:
    """Replace (or append) top-level scalar keys in YAML text.

    Only column-zero keys are rewritten. A nested ``integrity_sha256:`` — in a
    documented config schema, say — belongs to somebody else and must survive
    untouched. Keys the document does not declare are appended at the end.

    Args:
        text: The manifest source.
        fields: ``{key: already-rendered scalar}`` to write.

    Returns:
        The rewritten source. Every other line, comments included, is preserved
        verbatim.
    """
    remaining = dict(fields)
    out: list[str] = []
    for line in text.splitlines(keepends=True):
        # ``"integrity_sha256": ...`` is a legal top-level key; without the
        # strip it missed the match and appended a second, stale twin (YAML
        # takes the last, so nothing broke — the file just grew a liar).
        key = line.split(":", 1)[0].strip("\"'") if ":" in line else ""
        if key in remaining and not line[:1].isspace():
            out.append(f"{key}: {remaining.pop(key)}\n")
        else:
            out.append(line)
    body = "".join(out)
    if remaining:
        if body and not body.endswith("\n"):
            body += "\n"
        body += "".join(f"{key}: {value}\n" for key, value in remaining.items())
    return body


def _render_yaml_scalar(value: Any) -> str:
    """Render a field value as a YAML scalar.

    An empty string is quoted: a bare ``signature_ed25519:`` would parse back
    as ``None``, which reads like "never signed" rather than "deliberately
    blanked".
    """
    if isinstance(value, str):
        return value if value else '""'
    return str(value)


def set_manifest_fields(manifest: Path, fields: Mapping[str, Any]) -> None:
    """Write supply-chain fields into a manifest, preserving its formatting.

    Args:
        manifest: Path to ``manifest.yaml``/``.yml``/``.json``.
        fields: Keys and values to set, typically from
            :func:`supply_chain_fields`. A no-op when empty.

    Raises:
        OSError: The manifest cannot be read or written.
        ValueError: A ``.json`` manifest does not parse.
    """
    if not fields:
        return
    if manifest.suffix == ".json":
        data = json.loads(manifest.read_text(encoding="utf-8"))
        data.update(fields)
        manifest.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
        return
    rendered = {key: _render_yaml_scalar(value) for key, value in fields.items()}
    manifest.write_text(
        set_yaml_fields(manifest.read_text(encoding="utf-8"), rendered),
        encoding="utf-8",
    )


__all__ = [
    "HASH_KEY",
    "SIGNATURE_KEY",
    "SURFACE_KEY",
    "declared_hash",
    "set_manifest_fields",
    "set_yaml_fields",
    "stale_signature_present",
    "supply_chain_fields",
]

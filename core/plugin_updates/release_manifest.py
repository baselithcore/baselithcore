"""Signed per-file release manifest (update-apply spec §3, format 2).

The plugin signature covers the V5 hash surface only; docs, locales,
templates and — once releases carry one — a ``wheelhouse/`` of dependency
wheels travel outside it. ``release.json`` therefore lists the SHA-256 of
**every** regular file in the tarball (``files``, keyed by POSIX path relative
to the plugin directory) and carries its own Ed25519 signature,
``manifest_signature_ed25519``, over the canonical JSON of every other key,
domain-separated from plugin-hash signatures. A release without both is a
legacy release: shown, never installable.

Paths are compared byte for byte. A ``files`` key must be a plain relative
path (no empty, ``.`` or ``..`` segment, no leading ``/``, no backslash, no
control character), and two keys that would name one file on a
case-insensitive or Unicode-normalising filesystem are refused as ambiguous.
The publisher applies the same rules through :func:`file_digests`, so it can
never produce a list a deployment would refuse.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import stat
import unicodedata
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from core.plugins.signing import sign_message, verify_message

RELEASE_FORMAT = 2
FILES_KEY = "files"
SIGNATURE_KEY = "manifest_signature_ed25519"
#: A plugin-hash signature signs 64 ASCII hex chars; this prefix makes the two
#: message spaces disjoint, so neither signature can be replayed as the other.
_DOMAIN = b"baselith-release-manifest-v1\n"
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_LISTED_IN_DETAIL = 5
_SHOWN_PATH_CHARS = 120


class ReleaseManifestError(ValueError):
    """``release.json`` or a release tree does not fit the format."""


def _shown(rel: str) -> str:
    """``rel`` as it may appear in a refusal: printable and bounded."""
    text = rel if rel.isprintable() else repr(rel)
    if len(text) > _SHOWN_PATH_CHARS:
        text = text[:_SHOWN_PATH_CHARS] + "..."
    return text


def _safe_relpath(rel: str) -> bool:
    return (
        not rel.startswith("/")
        and "\\" not in rel
        and not any(unicodedata.category(ch) == "Cc" for ch in rel)
        and all(part not in ("", ".", "..") for part in rel.split("/"))
    )


def _fold(rel: str) -> str:
    """The name a case-insensitive, normalising filesystem would store."""
    return unicodedata.normalize("NFC", rel).casefold()


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _raise(exc: OSError) -> None:
    raise exc


def _digest_entry(path: Path, rel: str) -> str:
    info = path.lstat()
    if not stat.S_ISREG(info.st_mode):
        raise ReleaseManifestError(f"not a regular file: {_shown(rel)}")
    if info.st_nlink > 1:
        raise ReleaseManifestError(f"hard link in the release tree: {_shown(rel)}")
    if not _safe_relpath(rel):
        raise ReleaseManifestError(f"unsafe path in the release tree: {_shown(rel)}")
    return _sha256_file(path)


def file_digests(plugin_dir: Path) -> dict[str, str]:
    """SHA-256 of every regular file under ``plugin_dir``, keyed by POSIX relpath.

    Raises:
        ReleaseManifestError: A symlink, hard link, non-regular file, empty
            directory (the root included), a name :func:`parse_files` would
            refuse, or an unreadable entry.
    """
    out: dict[str, str] = {}
    try:
        for current, dirnames, filenames in os.walk(
            plugin_dir, followlinks=False, onerror=_raise
        ):
            base = Path(current)
            for name in dirnames:
                if (base / name).is_symlink():
                    rel = (base / name).relative_to(plugin_dir).as_posix()
                    raise ReleaseManifestError(
                        f"symlink in the release tree: {_shown(rel)}"
                    )
            if not dirnames and not filenames:
                # Invisible to a list of files, yet it can turn an import into
                # a namespace package or flip an ``is_dir()`` check.
                rel = base.relative_to(plugin_dir).as_posix()
                raise ReleaseManifestError(f"empty directory: {_shown(rel)}")
            dirnames.sort()
            for name in sorted(filenames):
                path = base / name
                rel = path.relative_to(plugin_dir).as_posix()
                out[rel] = _digest_entry(path, rel)
    except OSError as exc:
        raise ReleaseManifestError(
            f"release tree unreadable: {type(exc).__name__}"
        ) from exc
    return out


def canonical_bytes(meta: Mapping[str, Any]) -> bytes:
    """Sorted-key, whitespace-free ASCII JSON of ``meta`` minus its signature.

    Raises:
        ValueError: A value is not JSON-representable (NaN included).
    """
    body = {key: value for key, value in meta.items() if key != SIGNATURE_KEY}
    text = json.dumps(
        body, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False
    )
    return text.encode("ascii")


def sign_release_manifest(meta: Mapping[str, Any], private_key_hex: str) -> str:
    """Hex Ed25519 signature over the canonical form of ``meta``."""
    return sign_message(_DOMAIN + canonical_bytes(meta), private_key_hex)


def verify_release_manifest(
    meta: Mapping[str, Any], trusted_public_keys_hex: Sequence[str]
) -> bool:
    """True when ``meta``'s signature verifies against any trusted key."""
    signature = meta.get(SIGNATURE_KEY)
    if not isinstance(signature, str) or not signature:
        return False
    try:
        message = _DOMAIN + canonical_bytes(meta)
    except (TypeError, ValueError):
        return False
    return verify_message(message, signature, list(trusted_public_keys_hex))


def is_legacy(meta: Mapping[str, Any]) -> bool:
    """True for a release published before format 2 (no file list or signature)."""
    return FILES_KEY not in meta or SIGNATURE_KEY not in meta


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for key, value in pairs:
        if key in out:
            raise ReleaseManifestError(f"duplicate key in release.json: {_shown(key)}")
        out[key] = value
    return out


def _no_constant(name: str) -> Any:
    raise ReleaseManifestError(f"non-finite number in release.json: {name}")


def _no_float(text: str) -> Any:
    raise ReleaseManifestError("float in release.json: only integers are allowed")


def load_release_json(raw: bytes) -> dict[str, Any]:
    """Parse ``release.json`` strictly: UTF-8, one object, no duplicate keys.

    A duplicate key is refused because two parsers may keep different copies,
    NaN/Infinity because the canonical form cannot represent them, and any
    float because its canonical text would depend on the float repr of the
    signing and verifying Pythons.

    Raises:
        ReleaseManifestError: Anything but a strictly well-formed JSON object.
    """
    try:
        text = raw.decode("utf-8")
        data = json.loads(
            text,
            object_pairs_hook=_unique_object,
            parse_constant=_no_constant,
            parse_float=_no_float,
        )
    except ReleaseManifestError:
        raise
    except (UnicodeDecodeError, ValueError, RecursionError) as exc:
        raise ReleaseManifestError(
            f"release.json is not valid JSON: {type(exc).__name__}"
        ) from exc
    if not isinstance(data, dict):
        raise ReleaseManifestError("release.json is not an object")
    return data


def parse_files(meta: Mapping[str, Any], *, max_entries: int) -> dict[str, str]:
    """The validated ``files`` mapping of ``meta``.

    Raises:
        ReleaseManifestError: Not a non-empty mapping, more than
            ``max_entries`` entries, an unsafe or ambiguous path or a
            malformed digest.
    """
    raw = meta.get(FILES_KEY)
    if not isinstance(raw, dict) or not raw:
        raise ReleaseManifestError("files must be a non-empty mapping")
    if len(raw) > max_entries:
        raise ReleaseManifestError(f"files lists more than {max_entries} entries")
    out: dict[str, str] = {}
    folded: dict[str, str] = {}
    for rel, digest in raw.items():
        if not isinstance(rel, str) or not _safe_relpath(rel):
            raise ReleaseManifestError(f"unsafe path in files: {_shown(str(rel))}")
        if not isinstance(digest, str) or not _SHA256_RE.fullmatch(digest):
            raise ReleaseManifestError(
                f"files[{_shown(rel)}] is not a lowercase sha256"
            )
        twin = folded.setdefault(_fold(rel), rel)
        if twin != rel:
            raise ReleaseManifestError(
                f"ambiguous paths in files: {_shown(twin)} and {_shown(rel)}"
            )
        out[rel] = digest
    return out


def _listed(label: str, items: list[str]) -> str:
    shown = ", ".join(_shown(item) for item in items[:_LISTED_IN_DETAIL])
    more = len(items) - _LISTED_IN_DETAIL
    return f"{label}: {shown}" + (f" (+{more})" if more > 0 else "")


def files_mismatch(plugin_dir: Path, expected: Mapping[str, str]) -> str | None:
    """None when ``plugin_dir`` holds exactly ``expected``; else what differs."""
    try:
        actual = file_digests(plugin_dir)
    except ReleaseManifestError as exc:
        return str(exc)
    missing = sorted(set(expected) - set(actual))
    extra = sorted(set(actual) - set(expected))
    changed = sorted(p for p in set(expected) & set(actual) if expected[p] != actual[p])
    parts = [
        _listed(label, items)
        for label, items in (
            ("missing", missing),
            ("extra", extra),
            ("changed", changed),
        )
        if items
    ]
    return "; ".join(parts) or None


__all__ = [
    "FILES_KEY",
    "RELEASE_FORMAT",
    "SIGNATURE_KEY",
    "ReleaseManifestError",
    "canonical_bytes",
    "file_digests",
    "files_mismatch",
    "is_legacy",
    "load_release_json",
    "parse_files",
    "sign_release_manifest",
    "verify_release_manifest",
]

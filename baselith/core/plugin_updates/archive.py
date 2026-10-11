"""Unpack and verify a plugin release tarball exactly as a deployment does; shared by the checker, the release step and the updater."""

from __future__ import annotations

import hashlib
import posixpath
import tarfile
import tempfile
import unicodedata
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Any

from .models import Refusal, VerificationResult
from .verifier import verify_release

#: Members an update tarball may hold, and how many times the download cap its
#: unpacked size may reach: the archive is opened before its signature is
#: checked, so neither may be left to the publisher.
MAX_ARCHIVE_MEMBERS = 20_000
UNPACKED_SIZE_FACTOR = 4


class ArchiveLimitError(tarfile.TarError):
    """An update tarball holds too many members or unpacks too large."""


class UnsupportedMemberError(tarfile.TarError):
    """An update tarball holds a link, device or other non-regular member."""


def unpack_release(tarball: Path, dest: Path, max_bytes: int | None = None) -> None:
    """Extract ``tarball`` into ``dest``; unsafe members raise ``TarError``.

    Members are counted and sized from their headers before anything is
    written: more than :data:`MAX_ARCHIVE_MEMBERS`, or a declared total above
    ``max_bytes``, raises :class:`ArchiveLimitError`; any member that is not a
    regular file or a directory (a symbolic or hard link, a device, a FIFO),
    or whose path repeats another's up to case and Unicode normalisation,
    raises :class:`UnsupportedMemberError` — which of two such members wins
    depends on the filesystem, so the verified tree could differ from the one
    installed later.
    """
    dest.mkdir(parents=True, exist_ok=True)
    with tarfile.open(tarball, "r:gz") as tar:
        total = 0
        seen: set[str] = set()
        for count, member in enumerate(tar, start=1):
            if count > MAX_ARCHIVE_MEMBERS:
                raise ArchiveLimitError(f"more than {MAX_ARCHIVE_MEMBERS} members")
            if not (member.isfile() or member.isdir()):
                raise UnsupportedMemberError("a link or special file")
            folded = unicodedata.normalize(
                "NFC", posixpath.normpath(member.name)
            ).casefold()
            if folded in seen:
                raise UnsupportedMemberError("a duplicate or case-colliding member")
            seen.add(folded)
            total += max(member.size, 0)
            if max_bytes is not None and total > max_bytes:
                raise ArchiveLimitError(f"unpacks to more than {max_bytes} bytes")
        tar.extractall(dest, filter="data")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def verify_tarball(
    tarball: Path,
    plugin: str,
    version: str,
    installed: str | None,
    core_version: str,
    trusted_keys: Sequence[str],
    max_unpacked_bytes: int | None = None,
    expected_files: Mapping[str, str] | None = None,
    requirement_ok: Callable[[str], bool] | None = None,
) -> VerificationResult:
    extra: dict[str, Any] = (
        {} if requirement_ok is None else {"requirement_ok": requirement_ok}
    )
    with tempfile.TemporaryDirectory(prefix="plugin-update-") as tmp:
        root = Path(tmp) / "unpacked"
        try:
            unpack_release(tarball, root, max_unpacked_bytes)
        except ArchiveLimitError as exc:
            return _bad_archive(f"archive too large: {exc}")
        except UnsupportedMemberError as exc:
            return _bad_archive(f"archive holds {exc}")
        except (tarfile.TarError, OSError, EOFError) as exc:
            return _bad_archive(f"unpack failed: {type(exc).__name__}")
        entries = list(root.iterdir())
        if len(entries) != 1 or not entries[0].is_dir() or entries[0].is_symlink():
            return _bad_archive("tarball must hold a single plugin directory")
        return verify_release(
            entries[0],
            expected_name=plugin,
            expected_version=version,
            installed_version=installed,
            core_version=core_version,
            trusted_keys=trusted_keys,
            expected_files=expected_files,
            **extra,
        )


def verify_release_tarball(
    tarball: Path,
    plugin: str,
    version: str,
    core_version: str,
    trusted_keys: Sequence[str],
    *,
    expected_files: Mapping[str, str] | None = None,
    requirement_ok: Callable[[str], bool] | None = None,
) -> VerificationResult:
    """Unpack and verify a release tarball exactly as a deployment does.

    Shares the unpack (member types, case/NFC collisions, limits), the
    single-root rule and :func:`verify_release` with the update checker.
    ``requirement_ok`` overrides the dependency predicate (release-time
    callers must not judge the publisher's environment).
    """
    return verify_tarball(
        tarball,
        plugin,
        version,
        None,
        core_version,
        trusted_keys,
        expected_files=expected_files,
        requirement_ok=requirement_ok,
    )


def _bad_archive(detail: str) -> VerificationResult:
    return VerificationResult(refusal=Refusal.MANIFEST_INVALID, detail=detail)


__all__ = [
    "MAX_ARCHIVE_MEMBERS",
    "UNPACKED_SIZE_FACTOR",
    "ArchiveLimitError",
    "UnsupportedMemberError",
    "sha256_file",
    "unpack_release",
    "verify_release_tarball",
    "verify_tarball",
]

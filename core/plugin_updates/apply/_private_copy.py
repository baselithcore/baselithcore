"""Copy a pinned tarball through one descriptor; the copy is what gets verified."""

from __future__ import annotations

import hashlib
import hmac
import os
import stat
from pathlib import Path

_CHUNK = 1 << 20


class StagingError(Exception):
    """Staging refused; ``code`` is a run failure code, ``detail`` says why."""

    def __init__(self, code: str, detail: str = "") -> None:
        super().__init__(f"{code}: {detail}" if detail else code)
        self.code = code
        self.detail = detail


def private_copy(src: Path, dest: Path, expected_sha256: str) -> None:
    """Copy ``src`` into a new ``dest``, hashing the bytes as they are copied.

    ``src`` is opened once with ``O_NOFOLLOW`` (and ``O_NONBLOCK``, so a FIFO
    cannot stall the run) and must be a regular file; the digest is taken
    over exactly the bytes written to ``dest``, so the file verified later is
    the file that matched the pin, whatever happens to ``src`` meanwhile.

    Raises:
        StagingError: ``artifact_checksum`` — ``src`` is a link or not a
            regular file, cannot be read, or its digest differs from
            ``expected_sha256``. ``dest`` never survives a failure.
    """
    flags = os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | getattr(os, "O_CLOEXEC", 0)
    try:
        fd = os.open(src, flags)
    except OSError as exc:
        raise StagingError("artifact_checksum", "not a regular file") from exc
    digest = hashlib.sha256()
    created = False
    try:
        with os.fdopen(fd, "rb") as fin:
            if not stat.S_ISREG(os.fstat(fin.fileno()).st_mode):
                raise StagingError("artifact_checksum", "not a regular file")
            out_fd = os.open(
                dest, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600
            )
            created = True
            with os.fdopen(out_fd, "wb") as fout:
                for chunk in iter(lambda: fin.read(_CHUNK), b""):
                    digest.update(chunk)
                    fout.write(chunk)
                fout.flush()
                os.fsync(fout.fileno())
    except StagingError:
        raise
    except OSError as exc:
        if created:
            dest.unlink(missing_ok=True)
        raise StagingError("artifact_checksum", f"copy: {type(exc).__name__}") from exc
    if not hmac.compare_digest(digest.hexdigest(), expected_sha256.lower()):
        dest.unlink(missing_ok=True)
        raise StagingError(
            "artifact_checksum", "tarball differs from the approved release"
        )


__all__ = ["StagingError", "private_copy"]

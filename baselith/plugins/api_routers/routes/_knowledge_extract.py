"""Turn an uploaded file into text for the knowledge API.

Nothing about the file is trusted: not its name, not its declared type, not its
size. The name is reduced to a basename and used only as a label; the
extension (not the ``Content-Type``) selects the reader; the body is read in
chunks and refused the moment it passes the limit; and rich formats are parsed
from a private temporary directory under a name the client does not control.
"""

from __future__ import annotations

import asyncio
import os
import re
import tempfile
from pathlib import Path, PurePosixPath
from typing import Any

__all__ = [
    "ExtractionFailed",
    "RICH_SUFFIXES",
    "TEXT_SUFFIXES",
    "UnsupportedFile",
    "UploadTooLarge",
    "extract_text",
    "read_capped",
    "safe_filename",
]

#: Read as UTF-8 text.
TEXT_SUFFIXES = frozenset(
    {".txt", ".md", ".markdown", ".rst", ".csv", ".tsv", ".log", ".json"}
)
#: Parsed by the document readers, when that plugin is installed.
RICH_SUFFIXES = frozenset({".pdf", ".docx", ".xlsx", ".pptx"})

_CHUNK = 64 * 1024
_CONTROL = re.compile(r"[\x00-\x1f\x7f]")


class UploadTooLarge(ValueError):
    """The body passed the configured limit."""


class UnsupportedFile(ValueError):
    """The file type cannot be read here."""


class ExtractionFailed(ValueError):
    """The file is of a supported type but yielded no usable text."""


def safe_filename(name: str | None) -> str:
    """A label-safe basename: no directories, no control characters, bounded."""
    base = PurePosixPath((name or "").replace("\\", "/")).name
    cleaned = _CONTROL.sub("", base).strip()[:200]
    return "upload" if cleaned in ("", ".", "..") else cleaned


async def read_capped(upload: Any, limit: int) -> bytes:
    """Read an ``UploadFile`` body, failing as soon as it exceeds ``limit``.

    Raises:
        UploadTooLarge: More than ``limit`` bytes arrived.
    """
    chunks: list[bytes] = []
    total = 0
    while chunk := await upload.read(_CHUNK):
        total += len(chunk)
        if total > limit:
            raise UploadTooLarge(f"upload larger than {limit} bytes")
        chunks.append(chunk)
    return b"".join(chunks)


def _decode(data: bytes) -> str:
    if b"\x00" in data[:8192]:
        raise UnsupportedFile("the file looks binary, not text")
    try:
        return data.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise UnsupportedFile("text files must be UTF-8") from exc


def _read_rich(data: bytes, suffix: str) -> str | None:
    try:
        from baselith.plugins.document_sources import readers
    except ImportError as exc:
        raise UnsupportedFile(
            f"{suffix} files need the document_sources plugin"
        ) from exc
    reader = {
        ".pdf": readers.read_pdf,
        ".docx": readers.read_word,
        ".xlsx": readers.read_excel,
        ".pptx": readers.read_powerpoint,
    }[suffix]
    with tempfile.TemporaryDirectory(prefix="baselith-knowledge-") as workdir:
        target = Path(workdir) / f"upload{suffix}"
        fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
        return reader(target)


async def extract_text(data: bytes, filename: str) -> str:
    """The text of an uploaded file.

    Args:
        data: The file body, already size-capped.
        filename: The client's name for it; only its extension is used.

    Raises:
        UnsupportedFile: Unknown extension, binary or non-UTF-8 text, or a
            rich format whose reader is not installed.
        ExtractionFailed: The reader found no text (a scanned PDF without OCR,
            an encrypted or corrupt file).
    """
    suffix = Path(filename).suffix.lower()
    if suffix in TEXT_SUFFIXES:
        return _decode(data)
    if suffix in RICH_SUFFIXES:
        text = await asyncio.to_thread(_read_rich, data, suffix)
        if not text or not text.strip():
            raise ExtractionFailed(f"no text could be extracted from the {suffix} file")
        return text
    allowed = ", ".join(sorted(TEXT_SUFFIXES | RICH_SUFFIXES))
    raise UnsupportedFile(
        f"unsupported file type {suffix or '(none)'}; use one of {allowed}"
    )

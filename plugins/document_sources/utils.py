"""
Document Source Utilities.

Helper functions for path normalization, file extension checks, etc.
"""

from __future__ import annotations

import hashlib
import zipfile
from pathlib import Path

from core.observability.logging import get_logger

_MISSING_DEPENDENCIES: set[str] = set()

logger = get_logger(__name__)

#: Ceiling on the summed *uncompressed* size of an OOXML (docx/xlsx/pptx)
#: archive. Those formats are zip files that python-docx, openpyxl and
#: python-pptx inflate into memory; a few-kilobyte archive declaring gigabytes
#: of content would otherwise take the indexing worker down.
OOXML_MAX_UNCOMPRESSED_BYTES = 512 * 1024 * 1024
#: Ceiling on the number of members in one OOXML archive.
OOXML_MAX_MEMBERS = 10_000
#: Ceiling on any single member's compression ratio. Real Office parts
#: compress well (repetitive XML), but not by three orders of magnitude.
OOXML_MAX_MEMBER_RATIO = 1000


def strip_front_matter(text: str) -> str:
    """Rimuove eventuali blocchi YAML front matter dai documenti Markdown."""

    if not text.startswith("---"):
        return text

    lines = text.splitlines()
    closing_idx: int | None = None
    for idx, line in enumerate(lines[1:], start=1):
        if line.strip() == "---":
            closing_idx = idx
            break

    if closing_idx is None:
        return text

    remainder = lines[closing_idx + 1 :]
    return "\n".join(remainder)


def normalize_text(text: str) -> str:
    """Normalizza testo rimuovendo ritorni a capo inconsistenti e spazi extra."""

    return text.replace("\r\n", "\n").replace("\r", "\n").strip()


def warn_missing_dependency(name: str, feature: str) -> None:
    """Stampa un avviso la prima volta che una dipendenza opzionale manca."""

    if name in _MISSING_DEPENDENCIES:
        return
    logger.warning(f"Optional dependency missing: '{name}' (required for {feature})")
    _MISSING_DEPENDENCIES.add(name)


def compute_fingerprint(path: Path, content: str) -> str:
    """Genera un fingerprint considerando contenuto e timestamp del file."""

    digest = hashlib.sha256(content.encode("utf-8")).hexdigest()
    try:
        stamp = str(path.stat().st_mtime_ns)
    except OSError:
        stamp = "0"
    return f"{stamp}:{digest}"


def compute_remote_fingerprint(source_id: str, content: str) -> str:
    """Fingerprint per contenuti remoti, basato su URL canonico + hash."""

    normalized = (source_id or "").strip() or "remote"
    digest = hashlib.sha256(content.encode("utf-8")).hexdigest()
    return f"{normalized}:{digest}"


def ooxml_archive_is_safe(path: Path) -> bool:
    """Screen an OOXML zip before a parser inflates it (zip-bomb guard).

    Reads only the central directory — no member is decompressed. Declared
    sizes are what the parsers trust when they allocate, so bounding them
    bounds the parse.

    Args:
        path: The ``.docx``/``.xlsx``/``.pptx`` file.

    Returns:
        ``True`` when the archive is within :data:`OOXML_MAX_MEMBERS`,
        :data:`OOXML_MAX_UNCOMPRESSED_BYTES` and
        :data:`OOXML_MAX_MEMBER_RATIO`; ``False`` (logged) otherwise or when
        it is not a readable zip.
    """
    try:
        with zipfile.ZipFile(path) as archive:
            members = archive.infolist()
    except (OSError, zipfile.BadZipFile) as exc:
        logger.warning(f"[filesystem] Not a readable OOXML archive {path}: {exc}")
        return False
    if len(members) > OOXML_MAX_MEMBERS:
        logger.warning(f"[filesystem] Refusing {path}: {len(members)} zip members")
        return False
    total = 0
    for member in members:
        total += member.file_size
        ratio = member.file_size / max(member.compress_size, 1)
        if ratio > OOXML_MAX_MEMBER_RATIO and member.file_size > 1024 * 1024:
            logger.warning(
                f"[filesystem] Refusing {path}: member {member.filename!r} "
                f"compression ratio {ratio:.0f}:1"
            )
            return False
    if total > OOXML_MAX_UNCOMPRESSED_BYTES:
        logger.warning(
            f"[filesystem] Refusing {path}: {total} bytes uncompressed exceeds "
            f"{OOXML_MAX_UNCOMPRESSED_BYTES}"
        )
        return False
    return True

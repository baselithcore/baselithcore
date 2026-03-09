"""
Filesystem Document Source.

Implementation of DocumentSource for local directory and file scanning.
"""

from __future__ import annotations

import asyncio
from core.observability.logging import get_logger
from pathlib import Path
from typing import AsyncIterator, Dict, Iterator, Optional, Tuple

from core.config import get_processing_config
from core.nlp.spacy_utils import extract_spacy_metadata, is_spacy_available
from core.doc_sources.labels import build_kb_label

from .models import DocumentItem
from .registry import register_source
from . import readers
from .utils import compute_fingerprint

logger = get_logger(__name__)

_proc_config = get_processing_config()
DOCUMENTS_EXTENSIONS = _proc_config.documents_extensions
DOCUMENTS_ROOT = _proc_config.documents_root


class FilesystemDocumentSource:
    """Legge documenti dal filesystem locale (Markdown, PDF, Office, ...)."""

    MARKDOWN_EXTENSIONS = {".md", ".markdown"}
    WORD_EXTENSIONS = {".docx", ".doc"}
    EXCEL_EXTENSIONS = {".xlsx", ".xls"}
    POWERPOINT_EXTENSIONS = {".pptx", ".ppt"}
    PDF_EXTENSIONS = {".pdf"}
    IMAGE_EXTENSIONS = {
        ".png",
        ".jpg",
        ".jpeg",
        ".gif",
        ".webp",
        ".tif",
        ".tiff",
    }

    EXTENSION_DOC_TYPE = {
        ".md": "markdown",
        ".markdown": "markdown",
        ".pdf": "pdf",
        ".docx": "word",
        ".doc": "word",
        ".xlsx": "spreadsheet",
        ".xls": "spreadsheet",
        ".pptx": "presentation",
        ".ppt": "presentation",
        ".png": "image",
        ".jpg": "image",
        ".jpeg": "image",
        ".gif": "image",
        ".webp": "image",
        ".tif": "image",
        ".tiff": "image",
    }

    def __init__(
        self,
        root: Path | str | None = None,
        *,
        extensions: Tuple[str, ...] | None = None,
    ) -> None:
        """
        Initialize the filesystem document source.

        Args:
            root: Root directory to scan. Defaults to DOCUMENTS_ROOT from config.
            extensions: Tuple of allowed file extensions. Defaults to config settings.
        """
        if root is None:
            base_path = (
                Path(DOCUMENTS_ROOT)
                if isinstance(DOCUMENTS_ROOT, str)
                else DOCUMENTS_ROOT
            )
        else:
            base_path = Path(root).expanduser()
        if not base_path.is_absolute():
            base_path = Path.cwd() / base_path
        self._root = base_path
        allowed = extensions or DOCUMENTS_EXTENSIONS
        self._extensions = tuple(ext.lower() for ext in allowed)
        self._spacy_ready = is_spacy_available()

    def _iter_files_sync(self) -> Iterator[Path]:
        """
        Synchronously iterate over all valid files in the root directory.

        Yields:
            Path objects for each file that matches the allowed extensions.
        """
        if not self._root.exists():
            logger.warning(f"[filesystem] Document folder not found: {self._root}")
            return

        for path in sorted(self._root.rglob("*")):
            if not path.is_file():
                continue
            if path.suffix.lower() not in self._extensions:
                continue
            yield path

    def _read_file_sync(self, path: Path) -> Optional[str]:
        """
        Synchronously read and parse a file based on its extension.

        Args:
            path: Path to the file to read.

        Returns:
            Parsed string content or None if unsupported or failed.
        """
        # This function must be run in an executor
        suffix = path.suffix.lower()
        if suffix in self.MARKDOWN_EXTENSIONS:
            return readers.read_markdown(path)
        if suffix in self.PDF_EXTENSIONS:
            return readers.read_pdf(path)
        if suffix in self.WORD_EXTENSIONS:
            return readers.read_word(path)
        if suffix in self.EXCEL_EXTENSIONS:
            return readers.read_excel(path)
        if suffix in self.POWERPOINT_EXTENSIONS:
            return readers.read_powerpoint(path)
        if suffix in self.IMAGE_EXTENSIONS:
            return readers.read_image(path)
        logger.warning(f"[filesystem] Unsupported extension {suffix} for {path}")
        return None

    def _derive_title(self, content: str, path: Path) -> str:
        """
        Attempt to derive a meaningful title from document content or filename.

        Args:
            content: The text content of the document.
            path: The file path.

        Returns:
            A string title.
        """
        for line in content.splitlines():
            stripped = line.strip()
            if stripped.startswith("#"):
                return stripped.lstrip("#").strip() or path.stem
            if len(stripped) > 8:
                return stripped[:120].strip()
        return path.stem.replace("_", " ").replace("-", " ").strip() or path.stem

    def _document_metadata(self, path: Path, content: str) -> Dict[str, str]:
        """
        Extract metadata for the given document.

        Args:
            path: Path to the document.
            content: The text content of the document.

        Returns:
            A dictionary of metadata fields.
        """
        doc_type = self.EXTENSION_DOC_TYPE.get(path.suffix.lower(), "document")
        try:
            relative_path: str | Path = path.relative_to(self._root)
        except ValueError:
            relative_path = path.name

        metadata: Dict[str, str] = {
            "origin": "filesystem",
            "source": str(path),
            "relative_path": str(relative_path).replace("\\", "/"),
            "filename": path.name,
            "title": self._derive_title(content, path),
            "doc_type": doc_type,
        }

        try:
            parent_rel = (
                path.parent.relative_to(self._root)
                if path.parent != self._root
                else None
            )
        except ValueError:
            parent_rel = None
        if parent_rel:
            metadata["category"] = str(parent_rel).replace("\\", "/")

        if self._spacy_ready:
            spacy_metadata = extract_spacy_metadata(content)
            if spacy_metadata:
                metadata.update(spacy_metadata)

        return metadata

    def iter_headers(self) -> Iterator[Tuple[Path, float, int]]:
        """Yields (path, mtime, size_bytes) for all valid files."""
        for path in self._iter_files_sync():
            try:
                stat = path.stat()
                yield path, stat.st_mtime, stat.st_size
            except OSError:
                continue

    async def read_item(self, path: Path) -> Optional[DocumentItem]:
        """Reads and parses a single file item given its path."""
        # Security check: ensure path is within root
        try:
            path = path.resolve()
            if not str(path).startswith(str(self._root.resolve())):
                return None
        except Exception:
            return None

        loop = asyncio.get_running_loop()
        content = await loop.run_in_executor(None, self._read_file_sync, path)
        if not content:
            return None

        # Fingerprint computation can also be CPU intensive for large files
        fingerprint = await loop.run_in_executor(
            None, compute_fingerprint, path, content
        )
        metadata = self._document_metadata(path, content)

        return DocumentItem(
            uid=build_kb_label(path),
            content=content,
            fingerprint=fingerprint,
            metadata=metadata,
        )

    async def iter_items(self) -> AsyncIterator[DocumentItem]:
        """
        Scan the filesystem and yield document items.

        Yields:
            DocumentItem objects for each relevant file found.
        """
        # iter files sync is fast enough (filesystem metadata)
        # but reading them is slow and blocking.
        loop = asyncio.get_running_loop()
        files = await loop.run_in_executor(None, list, self._iter_files_sync())

        for path in files:
            item = await self.read_item(path)
            if item:
                yield item

    def close(self) -> None:
        """Release any resources held by the source."""
        return None


register_source("filesystem", FilesystemDocumentSource)

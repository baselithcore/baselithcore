"""
MinerU OCR Backend.

Primary OCR engine converting scanned PDFs and images to Markdown text via
MinerU (https://github.com/opendatalab/MinerU). MinerU emits whole-document
Markdown, so the output carries no per-page ``[Pagina N]`` markers (the
Tesseract fallback still emits them).

Documents can reach this path from untrusted sources (the web crawler fetches
remote PDFs into the same reader chain), so parsing is hardened against
resource-exhaustion: a byte cap and a page cap reject oversized input before
the heavy ML pipeline runs, ``do_parse`` executes on a **dedicated, bounded**
thread pool (never the shared default executor that request handling uses) with
a wall-clock timeout, and an ``*-http-client`` backend's ``server_url`` is
scheme/host-validated so document bytes are not shipped to a plaintext or
arbitrary remote endpoint.
"""

from __future__ import annotations

import io
import ipaddress
import os
import tempfile
import threading
import urllib.parse
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FuturesTimeoutError
from pathlib import Path
from typing import Any

from core.config import get_processing_config
from core.observability.logging import get_logger

from .utils import normalize_text, warn_missing_dependency

logger = get_logger(__name__)

_proc_config = get_processing_config()
MINERU_BACKEND = _proc_config.mineru_backend
MINERU_LANG = _proc_config.mineru_lang
MINERU_FORMULA_ENABLE = _proc_config.mineru_formula_enable
MINERU_TABLE_ENABLE = _proc_config.mineru_table_enable
MINERU_SERVER_URL = _proc_config.mineru_server_url
MINERU_MODEL_SOURCE = _proc_config.mineru_model_source
MINERU_MAX_BYTES = _proc_config.mineru_max_bytes
MINERU_MAX_PAGES = _proc_config.mineru_max_pages
MINERU_TIMEOUT_SECONDS = _proc_config.mineru_timeout_seconds
MINERU_MAX_CONCURRENCY = _proc_config.mineru_max_concurrency

# Fixed, filesystem-safe stem for do_parse output paths
# (do_parse uses the stems verbatim as directory/file names).
_DOC_STEM = "document"

# Backends that POST the document to ``server_url`` for remote inference.
_HTTP_CLIENT_BACKENDS = {"vlm-http-client", "hybrid-http-client"}

# Dedicated, lazily-created pool for the CPU/GPU-bound MinerU parse so it never
# competes with request-path work on the default executor. ``max_workers`` also
# bounds how many concurrent parses can run (memory/VRAM guard).
_ocr_pool: ThreadPoolExecutor | None = None
_ocr_pool_lock = threading.Lock()


def _get_ocr_pool() -> ThreadPoolExecutor:
    global _ocr_pool
    if _ocr_pool is None:
        with _ocr_pool_lock:
            if _ocr_pool is None:
                _ocr_pool = ThreadPoolExecutor(
                    max_workers=max(1, MINERU_MAX_CONCURRENCY),
                    thread_name_prefix="mineru-ocr",
                )
    return _ocr_pool


def run_pdf_ocr_mineru(path: Path) -> str | None:
    """Run MinerU OCR for PDF files."""
    return _perform_mineru_ocr(path)


def run_image_ocr_mineru(path: Path) -> str | None:
    """Run MinerU OCR for image files."""
    return _perform_mineru_ocr(path)


def _configure_mineru_env() -> None:
    """Configure environment variables for MinerU initialization."""
    if MINERU_MODEL_SOURCE:
        os.environ.setdefault("MINERU_MODEL_SOURCE", MINERU_MODEL_SOURCE)


def _is_internal_host(host: str) -> bool:
    """True for a loopback/private/link-local IP or a bare (dotless) service name."""
    if not host:
        return False
    if host == "localhost":
        return True
    try:
        ip = ipaddress.ip_address(host)
        return ip.is_loopback or ip.is_private or ip.is_link_local
    except ValueError:
        # A hostname: a bare (dotless) name is an internal service (docker/k8s);
        # a dotted FQDN is treated as public and must use https.
        return "." not in host


def _server_url_allowed(url: str | None) -> bool:
    """True if ``url`` is safe to ship document bytes to.

    Guards the ``*-http-client`` backends against exfiltrating document content
    to a plaintext or arbitrary remote endpoint (a typo'd or hostile URL): allow
    ``https`` anywhere, allow plaintext ``http`` only to an internal host
    (loopback, private range, or a dotless docker/k8s service name), and refuse
    every other scheme.
    """
    if not url:
        return False
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme == "https":
        return True
    if parsed.scheme == "http":
        return _is_internal_host(parsed.hostname or "")
    return False


def _pdf_page_count(pdf_bytes: bytes) -> int | None:
    """Best-effort page count from PDF bytes; None if it cannot be determined."""
    try:
        from pypdf import PdfReader

        return len(PdfReader(io.BytesIO(pdf_bytes)).pages)
    except Exception as exc:  # pragma: no cover - malformed/edge PDFs
        logger.debug("MinerU OCR: could not count pages: %s", exc)
        return None


def _oversized(path: Path, pdf_bytes: bytes) -> bool:
    """True (and logs) when the input exceeds the byte or page caps."""
    if MINERU_MAX_BYTES:
        try:
            size = path.stat().st_size
        except OSError:
            size = len(pdf_bytes)
        if size > MINERU_MAX_BYTES:
            logger.warning(
                "[filesystem] MinerU OCR: %s is %d bytes > MINERU_MAX_BYTES (%d); "
                "skipping",
                path,
                size,
                MINERU_MAX_BYTES,
            )
            return True
    if MINERU_MAX_PAGES:
        pages = _pdf_page_count(pdf_bytes)
        if pages is not None and pages > MINERU_MAX_PAGES:
            logger.warning(
                "[filesystem] MinerU OCR: %s has %d pages > MINERU_MAX_PAGES (%d); "
                "skipping",
                path,
                pages,
                MINERU_MAX_PAGES,
            )
            return True
    return False


def _invoke_do_parse(do_parse: Any, tmp_dir: str, pdf_bytes: bytes) -> None:
    """Call ``do_parse`` for a single document (runs on the dedicated pool)."""
    do_parse(
        output_dir=tmp_dir,
        pdf_file_names=[_DOC_STEM],
        pdf_bytes_list=[pdf_bytes],
        p_lang_list=[MINERU_LANG],
        backend=MINERU_BACKEND,
        parse_method="auto",
        formula_enable=MINERU_FORMULA_ENABLE,
        table_enable=MINERU_TABLE_ENABLE,
        server_url=MINERU_SERVER_URL,
        f_draw_layout_bbox=False,
        f_draw_span_bbox=False,
        f_dump_md=True,
        f_dump_middle_json=False,
        f_dump_model_output=False,
        f_dump_orig_pdf=False,
        f_dump_content_list=False,
    )


def _perform_mineru_ocr(path: Path) -> str | None:
    """
    Execute OCR using the MinerU engine.

    Args:
        path: Path to the PDF or image file.

    Returns:
        Whole-document Markdown text or None if failed / rejected.
    """
    if MINERU_BACKEND in _HTTP_CLIENT_BACKENDS and not _server_url_allowed(
        MINERU_SERVER_URL
    ):
        logger.error(
            "[filesystem] MinerU OCR: backend %s needs a safe MINERU_SERVER_URL "
            "(https, or http on loopback); refusing to send document bytes",
            MINERU_BACKEND,
        )
        return None

    try:
        _configure_mineru_env()
        from mineru.cli.common import do_parse, read_fn
    except ImportError:  # pragma: no cover - dipendenza opzionale
        warn_missing_dependency("mineru", "OCR (MinerU)")
        return None
    except Exception as exc:  # pragma: no cover - setup fallito
        logger.warning(f"[filesystem] Failed to initialize MinerU OCR: {exc}")
        return None

    try:
        # read_fn also converts image inputs into single-page PDF bytes.
        pdf_bytes = read_fn(path)
    except Exception as exc:
        logger.warning(
            f"[filesystem] MinerU OCR: error preparing input from {path}: {exc}"
        )
        return None

    if _oversized(path, pdf_bytes):
        return None

    with tempfile.TemporaryDirectory(prefix="mineru-ocr-") as tmp_dir:
        future = _get_ocr_pool().submit(_invoke_do_parse, do_parse, tmp_dir, pdf_bytes)
        try:
            future.result(timeout=MINERU_TIMEOUT_SECONDS or None)
        except FuturesTimeoutError:
            logger.warning(
                "[filesystem] MinerU OCR: timed out after %.0fs on %s",
                MINERU_TIMEOUT_SECONDS,
                path,
            )
            return None
        except Exception as exc:
            logger.warning(
                f"[filesystem] MinerU OCR: inference failed on {path}: {exc}"
            )
            return None
        text = _read_markdown_output(Path(tmp_dir))

    if not text:
        logger.warning(f"[filesystem] MinerU OCR produced no text for {path}")
        return None
    return text


def _read_markdown_output(output_dir: Path) -> str | None:
    """
    Locate and read the Markdown produced by do_parse.

    The output subdirectory is backend/method dependent (e.g.
    ``document/auto/document.md`` for the pipeline backend), hence the glob.

    Args:
        output_dir: Root output directory passed to do_parse.

    Returns:
        Normalized Markdown text or None if missing/empty.
    """
    md_path = next(output_dir.glob(f"{_DOC_STEM}/*/{_DOC_STEM}.md"), None)
    if md_path is None:
        return None
    try:
        raw = md_path.read_text(encoding="utf-8")
    except OSError as exc:
        logger.warning(f"[filesystem] MinerU OCR: cannot read output {md_path}: {exc}")
        return None
    return normalize_text(raw) or None


__all__ = ["run_image_ocr_mineru", "run_pdf_ocr_mineru"]

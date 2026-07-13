"""
MinerU OCR Backend.

Primary OCR engine converting scanned PDFs and images to Markdown text via
MinerU (https://github.com/opendatalab/MinerU). MinerU emits whole-document
Markdown, so the output carries no per-page ``[Pagina N]`` markers (the
Tesseract fallback still emits them).
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

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

# Fixed, filesystem-safe stem for do_parse output paths
# (do_parse uses the stems verbatim as directory/file names).
_DOC_STEM = "document"


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


def _perform_mineru_ocr(path: Path) -> str | None:
    """
    Execute OCR using the MinerU engine.

    Args:
        path: Path to the PDF or image file.

    Returns:
        Whole-document Markdown text or None if failed.
    """
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

    with tempfile.TemporaryDirectory(prefix="mineru-ocr-") as tmp_dir:
        try:
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

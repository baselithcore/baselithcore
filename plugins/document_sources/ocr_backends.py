"""
OCR Backends.

Integration with various OCR engines for processing non-searchable PDFs and images.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from core.config import get_processing_config
from core.observability.logging import get_logger

from .ocr_mineru import run_image_ocr_mineru, run_pdf_ocr_mineru
from .utils import normalize_text, warn_missing_dependency

if TYPE_CHECKING:  # pragma: no cover - solo per type checkers
    from PIL import Image

logger = get_logger(__name__)

_proc_config = get_processing_config()
PDF_OCR_BACKEND = _proc_config.pdf_ocr_backend


def run_pdf_ocr(path: Path) -> str | None:
    """Esegue OCR su PDF scannerizzati."""

    for backend in _select_ocr_backends():
        if backend == "mineru":
            result = run_pdf_ocr_mineru(path)
        else:
            result = _run_pdf_ocr_tesseract(path)
        if result:
            _log_backend_fallback(backend, path)
            return result
    return None


def run_image_ocr(path: Path) -> str | None:
    """Esegue OCR su immagini supportate."""

    for backend in _select_ocr_backends():
        if backend == "mineru":
            result = run_image_ocr_mineru(path)
        else:
            result = _run_image_ocr_tesseract(path)
        if result:
            _log_backend_fallback(backend, path)
            return result
    return None


def _select_ocr_backends() -> list[str]:
    """
    Select the appropriate OCR backends based on configuration.

    Returns:
        A prioritized list of backend names (e.g., ["mineru", "tesseract"]).
    """
    if PDF_OCR_BACKEND == "tesseract":
        return ["tesseract"]
    return ["mineru", "tesseract"]


def _log_backend_fallback(backend: str, path: Path) -> None:
    """
    Log a message if a fallback OCR backend is used.

    Args:
        backend: The backend name that was actually used.
        path: The path to the file being processed.
    """
    if PDF_OCR_BACKEND != "auto" and backend != PDF_OCR_BACKEND:
        logger.info(f"[filesystem] Fallback OCR backend '{backend}' used for {path}")


def _run_pdf_ocr_tesseract(path: Path) -> str | None:
    """Run Tesseract OCR for PDF files by converting to images first."""
    try:
        from pdf2image import convert_from_path
    except ImportError:  # pragma: no cover - dipendenza opzionale
        warn_missing_dependency("pdf2image", "OCR PDF")
        return None

    try:
        images = convert_from_path(str(path))
    except Exception as exc:  # pragma: no cover - conversione fallita
        logger.warning(
            f"[filesystem] Failed to convert PDF to images for OCR {path}: {exc}"
        )
        return None

    try:
        text = _extract_text_with_tesseract(images, path, "Pagina")
    finally:
        for image in images:
            try:
                image.close()
            except Exception as e:  # pragma: no cover - chiusura best effort
                logger.warning(f"[filesystem] Error closing image: {e}")
    return text


def _run_image_ocr_tesseract(path: Path) -> str | None:
    """Run Tesseract OCR for image files."""
    try:
        from PIL import Image
    except ImportError:  # pragma: no cover - dipendenza opzionale
        warn_missing_dependency("Pillow", "lettura immagini per OCR")
        return None

    try:
        image = Image.open(path)
    except Exception as exc:
        logger.warning(f"[filesystem] Failed to open image {path} for OCR: {exc}")
        return None

    try:
        return _extract_text_with_tesseract([image], path, "Immagine")
    finally:
        try:
            image.close()
        except Exception as e:
            logger.warning(f"[filesystem] Error closing image: {e}")


def _extract_text_with_tesseract(
    images: list[Image.Image], path: Path, page_label: str
) -> str | None:
    """
    Internal helper to extract text from a list of images using Tesseract.

    Args:
        images: List of PIL Image objects.
        path: Original file path (for logging).
        page_label: Label to use for pages.

    Returns:
        Combined text or None if failed.
    """
    try:
        import pytesseract
    except ImportError:  # pragma: no cover - dipendenza opzionale
        warn_missing_dependency("pytesseract", "OCR Tesseract")
        return None

    text_parts: list[str] = []
    for page_index, image in enumerate(images, start=1):
        try:
            snippet = pytesseract.image_to_string(image)
        except pytesseract.TesseractNotFoundError:  # pragma: no cover
            warn_missing_dependency("tesseract", "OCR")
            return None
        except Exception as exc:
            logger.warning(f"[filesystem] OCR Tesseract: error on {path}: {exc}")
            return None
        snippet = normalize_text(snippet)
        if snippet:
            text_parts.append(f"[{page_label} {page_index}]\n{snippet}")

    combined = "\n\n".join(text_parts)
    if not combined.strip():
        logger.warning(f"[filesystem] OCR Tesseract produced no text for {path}")
        return None
    return combined


__all__ = ["run_pdf_ocr", "run_image_ocr"]

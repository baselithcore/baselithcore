"""
OCR Backends.

Integration with various OCR engines for processing non-searchable PDFs and images.
"""

from __future__ import annotations

from core.observability.logging import get_logger
import os
from pathlib import Path

from typing import Any, Dict, List, Optional, TYPE_CHECKING

from core.config import get_processing_config
from .utils import normalize_text, warn_missing_dependency

if TYPE_CHECKING:  # pragma: no cover - solo per type checkers
    from PIL import Image

logger = get_logger(__name__)

_proc_config = get_processing_config()
CHANDRA_INCLUDE_HEADERS_FOOTERS = _proc_config.chandra_include_headers_footers
CHANDRA_MAX_OUTPUT_TOKENS = _proc_config.chandra_max_output_tokens
CHANDRA_MAX_RETRIES = _proc_config.chandra_max_retries
CHANDRA_MAX_WORKERS = _proc_config.chandra_max_workers
CHANDRA_OCR_METHOD = _proc_config.chandra_ocr_method
CHANDRA_VLLM_API_BASE = _proc_config.chandra_vllm_api_base
CHANDRA_VLLM_API_KEY = _proc_config.chandra_vllm_api_key
CHANDRA_VLLM_MODEL_NAME = _proc_config.chandra_vllm_model_name
PDF_OCR_BACKEND = _proc_config.pdf_ocr_backend


def run_pdf_ocr(path: Path) -> Optional[str]:
    """Esegue OCR su PDF scannerizzati."""

    for backend in _select_ocr_backends():
        if backend == "chandra":
            result = _run_pdf_ocr_chandra(path)
        else:
            result = _run_pdf_ocr_tesseract(path)
        if result:
            _log_backend_fallback(backend, path)
            return result
    return None


def run_image_ocr(path: Path) -> Optional[str]:
    """Esegue OCR su immagini supportate."""

    for backend in _select_ocr_backends():
        if backend == "chandra":
            result = _run_image_ocr_chandra(path)
        else:
            result = _run_image_ocr_tesseract(path)
        if result:
            _log_backend_fallback(backend, path)
            return result
    return None


def _select_ocr_backends() -> List[str]:
    """
    Select the appropriate OCR backends based on configuration.

    Returns:
        A prioritized list of backend names (e.g., ["chandra", "tesseract"]).
    """
    if PDF_OCR_BACKEND == "tesseract":
        return ["tesseract"]
    if PDF_OCR_BACKEND == "auto":
        return ["chandra", "tesseract"]
    return ["chandra", "tesseract"]


def _log_backend_fallback(backend: str, path: Path) -> None:
    """
    Log a message if a fallback OCR backend is used.

    Args:
        backend: The backend name that was actually used.
        path: The path to the file being processed.
    """
    if PDF_OCR_BACKEND != "auto" and backend != PDF_OCR_BACKEND:
        logger.info(f"[filesystem] Fallback OCR backend '{backend}' used for {path}")


def _configure_chandra_env() -> None:
    """Configure environment variables for Chandra OCR initialization."""
    if CHANDRA_VLLM_API_BASE:
        os.environ.setdefault("VLLM_API_BASE", CHANDRA_VLLM_API_BASE)
    if CHANDRA_VLLM_API_KEY:
        os.environ.setdefault("VLLM_API_KEY", CHANDRA_VLLM_API_KEY)
    if CHANDRA_VLLM_MODEL_NAME:
        os.environ.setdefault("VLLM_MODEL_NAME", CHANDRA_VLLM_MODEL_NAME)


def _perform_chandra_ocr(path: Path, page_label: str) -> Optional[str]:
    """
    Execute OCR using the Chandra engine.

    Args:
        path: Path to the file.
        page_label: Label to use for pages (e.g., "Pagina", "Immagine").

    Returns:
        Combined OCR text or None if failed.
    """
    try:
        _configure_chandra_env()
        from chandra.input import load_file  # type: ignore[import]
        from chandra.model import InferenceManager  # type: ignore[import]
        from chandra.model.schema import BatchInputItem  # type: ignore[import]
    except ImportError:  # pragma: no cover - dipendenza opzionale
        warn_missing_dependency("chandra-ocr", "OCR (Chandra)")
        return None
    except Exception as exc:  # pragma: no cover - setup fallito
        logger.warning(f"[filesystem] Failed to configure Chandra OCR: {exc}")
        return None

    try:
        images = load_file(str(path), {})
    except Exception as exc:
        logger.warning(
            f"[filesystem] Chandra OCR: error preparing images from {path}: {exc}"
        )
        return None

    if not images:
        logger.warning(f"[filesystem] Chandra OCR: no images extracted from {path}")
        return None

    try:
        manager = InferenceManager(method=CHANDRA_OCR_METHOD)
    except Exception as exc:
        logger.warning(f"[filesystem] Chandra OCR: failed to initialize model ({exc})")
        return None

    batch = [BatchInputItem(image=img, prompt_type="ocr_layout") for img in images]

    generate_kwargs: Dict[str, Any] = {
        "include_images": False,
        "include_headers_footers": CHANDRA_INCLUDE_HEADERS_FOOTERS,
    }
    if CHANDRA_MAX_OUTPUT_TOKENS:
        generate_kwargs["max_output_tokens"] = int(CHANDRA_MAX_OUTPUT_TOKENS)
    if CHANDRA_MAX_RETRIES and CHANDRA_OCR_METHOD == "vllm":
        generate_kwargs["max_retries"] = int(CHANDRA_MAX_RETRIES)
    if CHANDRA_MAX_WORKERS and CHANDRA_OCR_METHOD == "vllm":
        generate_kwargs["max_workers"] = int(CHANDRA_MAX_WORKERS)

    try:
        results = manager.generate(batch, **generate_kwargs)
    except Exception as exc:
        logger.warning(f"[filesystem] Chandra OCR: inference failed on {path}: {exc}")
        return None
    finally:
        for img in images:
            try:
                img.close()
            except Exception as e:  # pragma: no cover - chiusura best effort
                logger.warning(f"[filesystem] Error closing image: {e}")

    text_parts: List[str] = []
    for page_index, result in enumerate(results, start=1):
        if getattr(result, "error", False):
            continue
        raw_text = result.markdown or result.html or result.raw or ""
        snippet = normalize_text(raw_text)
        if not snippet:
            continue
        text_parts.append(f"[{page_label} {page_index}]\n{snippet}")

    combined = "\n\n".join(text_parts)
    if not combined.strip():
        logger.warning(f"[filesystem] Chandra OCR produced no text for {path}")
        return None
    return combined


def _run_pdf_ocr_chandra(path: Path) -> Optional[str]:
    """Run Chandra OCR specifically for PDF files."""
    return _perform_chandra_ocr(path, "Pagina")


def _run_image_ocr_chandra(path: Path) -> Optional[str]:
    """Run Chandra OCR specifically for image files."""
    return _perform_chandra_ocr(path, "Immagine")


def _run_pdf_ocr_tesseract(path: Path) -> Optional[str]:
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


def _run_image_ocr_tesseract(path: Path) -> Optional[str]:
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
    images: List["Image.Image"], path: Path, page_label: str
) -> Optional[str]:
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

    text_parts: List[str] = []
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

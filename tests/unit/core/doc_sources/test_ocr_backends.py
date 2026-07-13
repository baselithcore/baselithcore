"""Tests for OCR backend selection and fallback chain."""

from pathlib import Path
from unittest.mock import patch

import pytest

from core.doc_sources import ocr_backends


@pytest.fixture
def sample_pdf_path():
    return Path("/tmp/sample_ocr_file.pdf")


@pytest.fixture
def sample_image_path():
    return Path("/tmp/sample_ocr_file.png")


class TestSelectOcrBackends:
    """Tests for _select_ocr_backends."""

    def test_auto_returns_mineru_then_tesseract(self):
        with patch.object(ocr_backends, "PDF_OCR_BACKEND", "auto"):
            assert ocr_backends._select_ocr_backends() == ["mineru", "tesseract"]

    def test_mineru_returns_mineru_then_tesseract(self):
        with patch.object(ocr_backends, "PDF_OCR_BACKEND", "mineru"):
            assert ocr_backends._select_ocr_backends() == ["mineru", "tesseract"]

    def test_tesseract_returns_tesseract_only(self):
        with patch.object(ocr_backends, "PDF_OCR_BACKEND", "tesseract"):
            assert ocr_backends._select_ocr_backends() == ["tesseract"]


class TestRunPdfOcr:
    """Tests for run_pdf_ocr dispatch and fallback."""

    def test_mineru_success_skips_tesseract(self, sample_pdf_path):
        with (
            patch.object(ocr_backends, "PDF_OCR_BACKEND", "mineru"),
            patch.object(
                ocr_backends, "run_pdf_ocr_mineru", return_value="mineru text"
            ) as mock_mineru,
            patch.object(ocr_backends, "_run_pdf_ocr_tesseract") as mock_tesseract,
        ):
            assert ocr_backends.run_pdf_ocr(sample_pdf_path) == "mineru text"
            mock_mineru.assert_called_once_with(sample_pdf_path)
            mock_tesseract.assert_not_called()

    def test_falls_back_to_tesseract_when_mineru_fails(self, sample_pdf_path):
        with (
            patch.object(ocr_backends, "PDF_OCR_BACKEND", "auto"),
            patch.object(
                ocr_backends, "run_pdf_ocr_mineru", return_value=None
            ) as mock_mineru,
            patch.object(
                ocr_backends, "_run_pdf_ocr_tesseract", return_value="tesseract text"
            ) as mock_tesseract,
        ):
            assert ocr_backends.run_pdf_ocr(sample_pdf_path) == "tesseract text"
            mock_mineru.assert_called_once_with(sample_pdf_path)
            mock_tesseract.assert_called_once_with(sample_pdf_path)

    def test_returns_none_when_all_backends_fail(self, sample_pdf_path):
        with (
            patch.object(ocr_backends, "PDF_OCR_BACKEND", "auto"),
            patch.object(ocr_backends, "run_pdf_ocr_mineru", return_value=None),
            patch.object(ocr_backends, "_run_pdf_ocr_tesseract", return_value=None),
        ):
            assert ocr_backends.run_pdf_ocr(sample_pdf_path) is None

    def test_tesseract_only_skips_mineru(self, sample_pdf_path):
        with (
            patch.object(ocr_backends, "PDF_OCR_BACKEND", "tesseract"),
            patch.object(ocr_backends, "run_pdf_ocr_mineru") as mock_mineru,
            patch.object(
                ocr_backends, "_run_pdf_ocr_tesseract", return_value="tesseract text"
            ),
        ):
            assert ocr_backends.run_pdf_ocr(sample_pdf_path) == "tesseract text"
            mock_mineru.assert_not_called()


class TestRunImageOcr:
    """Tests for run_image_ocr dispatch and fallback."""

    def test_mineru_success_skips_tesseract(self, sample_image_path):
        with (
            patch.object(ocr_backends, "PDF_OCR_BACKEND", "mineru"),
            patch.object(
                ocr_backends, "run_image_ocr_mineru", return_value="mineru text"
            ) as mock_mineru,
            patch.object(ocr_backends, "_run_image_ocr_tesseract") as mock_tesseract,
        ):
            assert ocr_backends.run_image_ocr(sample_image_path) == "mineru text"
            mock_mineru.assert_called_once_with(sample_image_path)
            mock_tesseract.assert_not_called()

    def test_falls_back_to_tesseract_when_mineru_fails(self, sample_image_path):
        with (
            patch.object(ocr_backends, "PDF_OCR_BACKEND", "auto"),
            patch.object(ocr_backends, "run_image_ocr_mineru", return_value=None),
            patch.object(
                ocr_backends,
                "_run_image_ocr_tesseract",
                return_value="tesseract text",
            ),
        ):
            assert ocr_backends.run_image_ocr(sample_image_path) == "tesseract text"

    def test_returns_none_when_all_backends_fail(self, sample_image_path):
        with (
            patch.object(ocr_backends, "PDF_OCR_BACKEND", "auto"),
            patch.object(ocr_backends, "run_image_ocr_mineru", return_value=None),
            patch.object(ocr_backends, "_run_image_ocr_tesseract", return_value=None),
        ):
            assert ocr_backends.run_image_ocr(sample_image_path) is None

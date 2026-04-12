"""Backward-compatible shim for the Document Sources OCR backends module."""

import sys

import plugins.document_sources.ocr_backends as _ocr_backends

sys.modules[__name__] = _ocr_backends

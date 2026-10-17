"""Backward-compatible shim for the Document Sources OCR backends module."""

import sys

import baselith.plugins.document_sources.readers.ocr_backends as _ocr_backends

sys.modules[__name__] = _ocr_backends

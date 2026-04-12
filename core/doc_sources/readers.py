"""Backward-compatible shim for the Document Sources readers module."""

import sys

import plugins.document_sources.readers as _readers

sys.modules[__name__] = _readers

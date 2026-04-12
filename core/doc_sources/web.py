"""Backward-compatible shim for the Document Sources web module."""

import sys

import plugins.document_sources.web as _web

sys.modules[__name__] = _web

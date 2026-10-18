"""Backward-compatible shim for the Document Sources web module."""

import sys

import baselith.plugins.document_sources.sources.web as _web

sys.modules[__name__] = _web

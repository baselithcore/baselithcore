"""Backward-compatible shim for the Document Sources web constants module."""

import sys

import plugins.document_sources.web_constants as _web_constants

sys.modules[__name__] = _web_constants

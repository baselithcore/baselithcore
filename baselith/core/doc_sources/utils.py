"""Backward-compatible shim for the Document Sources utils module."""

import sys

import baselith.plugins.document_sources.utils as _utils

sys.modules[__name__] = _utils

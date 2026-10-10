"""Backward-compatible shim for the Document Sources models module."""

import sys

import baselith.plugins.document_sources.models as _models

sys.modules[__name__] = _models

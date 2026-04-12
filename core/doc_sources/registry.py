"""Backward-compatible shim for the Document Sources registry module."""

import sys

import plugins.document_sources.registry as _registry

sys.modules[__name__] = _registry

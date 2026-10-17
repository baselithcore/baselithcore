"""Backward-compatible shim for the Document Sources web parser module."""

import sys

import baselith.plugins.document_sources.sources.web_parser as _web_parser

sys.modules[__name__] = _web_parser

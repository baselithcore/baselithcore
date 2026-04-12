"""Backward-compatible shim for the Document Sources web parser module."""

import sys

import plugins.document_sources.web_parser as _web_parser

sys.modules[__name__] = _web_parser

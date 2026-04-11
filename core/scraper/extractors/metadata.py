"""Backward-compatible shim for the Web Scraper metadata extractor module."""

import sys

import plugins.web_scraper.extractors.metadata as _metadata

sys.modules[__name__] = _metadata

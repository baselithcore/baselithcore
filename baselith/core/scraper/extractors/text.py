"""Backward-compatible shim for the Web Scraper text extractor module."""

import sys

import baselith.plugins.web_scraper.extractors.text as _text

sys.modules[__name__] = _text

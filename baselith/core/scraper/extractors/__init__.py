"""Backward-compatible shim for the Web Scraper extractors package."""

import sys

import baselith.plugins.web_scraper.extractors as _extractors

sys.modules[__name__] = _extractors

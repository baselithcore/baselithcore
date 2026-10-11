"""Backward-compatible shim for the Web Scraper CSS extractor module."""

import sys

import baselith.plugins.web_scraper.extractors.css_selector as _css_selector

sys.modules[__name__] = _css_selector

"""Backward-compatible shim for the Web Scraper base extractor module."""

import sys

import baselith.plugins.web_scraper.extractors.base as _base

sys.modules[__name__] = _base

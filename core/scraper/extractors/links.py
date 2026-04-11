"""Backward-compatible shim for the Web Scraper link extractor module."""

import sys

import plugins.web_scraper.extractors.links as _links

sys.modules[__name__] = _links

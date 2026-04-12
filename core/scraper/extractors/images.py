"""Backward-compatible shim for the Web Scraper image extractor module."""

import sys

import plugins.web_scraper.extractors.images as _images

sys.modules[__name__] = _images

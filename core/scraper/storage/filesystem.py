"""Backward-compatible shim for the Web Scraper filesystem storage module."""

import sys

import plugins.web_scraper.storage.filesystem as _filesystem

sys.modules[__name__] = _filesystem

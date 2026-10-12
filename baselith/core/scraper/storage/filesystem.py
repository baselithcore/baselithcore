"""Backward-compatible shim for the Web Scraper filesystem storage module."""

import sys

import baselith.plugins.web_scraper.storage.filesystem as _filesystem

sys.modules[__name__] = _filesystem

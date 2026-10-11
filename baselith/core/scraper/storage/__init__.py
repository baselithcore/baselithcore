"""Backward-compatible shim for the Web Scraper storage package."""

import sys

import baselith.plugins.web_scraper.storage as _storage

sys.modules[__name__] = _storage

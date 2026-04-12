"""Backward-compatible shim for the Web Scraper cache middleware module."""

import sys

import plugins.web_scraper.middleware.cache as _cache

sys.modules[__name__] = _cache

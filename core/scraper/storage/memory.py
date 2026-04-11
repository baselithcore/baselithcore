"""Backward-compatible shim for the Web Scraper memory storage module."""

import sys

import plugins.web_scraper.storage.memory as _memory

sys.modules[__name__] = _memory

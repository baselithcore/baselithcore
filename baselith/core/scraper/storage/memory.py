"""Backward-compatible shim for the Web Scraper memory storage module."""

import sys

import baselith.plugins.web_scraper.storage.memory as _memory

sys.modules[__name__] = _memory

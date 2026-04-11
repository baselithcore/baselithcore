"""Backward-compatible shim for the Web Scraper base storage module."""

import sys

import plugins.web_scraper.storage.base as _base

sys.modules[__name__] = _base

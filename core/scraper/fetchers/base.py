"""Backward-compatible shim for the Web Scraper base fetcher module."""

import sys

import plugins.web_scraper.fetchers.base as _base

sys.modules[__name__] = _base

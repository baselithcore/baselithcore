"""Backward-compatible shim for the Web Scraper fetchers package."""

import sys

import plugins.web_scraper.fetchers as _fetchers

sys.modules[__name__] = _fetchers

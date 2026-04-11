"""Backward-compatible shim for the Web Scraper plugin package."""

import sys

import plugins.web_scraper as _web_scraper

sys.modules[__name__] = _web_scraper

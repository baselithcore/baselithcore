"""Backward-compatible shim for the Web Scraper tools module."""

import sys

import plugins.web_scraper.tools as _tools

sys.modules[__name__] = _tools

"""Backward-compatible shim for the Web Scraper facade module."""

import sys

import plugins.web_scraper.scraper as _scraper
from plugins.web_scraper.scraper import Scraper

# Register self as the plugin module for runtime compatibility
sys.modules[__name__] = _scraper

__all__ = ["Scraper"]

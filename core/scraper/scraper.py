"""Backward-compatible shim for the Web Scraper facade module."""

import sys

from plugins.web_scraper.scraper import Scraper
import plugins.web_scraper.scraper as _scraper

# Register self as the plugin module for runtime compatibility
sys.modules[__name__] = _scraper

__all__ = ["Scraper"]

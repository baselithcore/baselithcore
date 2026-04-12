"""Backward-compatible shim for the Web Scraper protocols module."""

import sys

import plugins.web_scraper.protocols as _protocols

sys.modules[__name__] = _protocols

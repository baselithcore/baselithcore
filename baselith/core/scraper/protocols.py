"""Backward-compatible shim for the Web Scraper protocols module."""

import sys

import baselith.plugins.web_scraper.engine.protocols as _protocols

sys.modules[__name__] = _protocols

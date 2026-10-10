"""Backward-compatible shim for the Web Scraper middleware package."""

import sys

import baselith.plugins.web_scraper.middleware as _middleware

sys.modules[__name__] = _middleware

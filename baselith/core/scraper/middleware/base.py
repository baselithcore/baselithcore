"""Backward-compatible shim for the Web Scraper base middleware module."""

import sys

import baselith.plugins.web_scraper.middleware.base as _base

sys.modules[__name__] = _base

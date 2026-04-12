"""Backward-compatible shim for the Web Scraper retry middleware module."""

import sys

import plugins.web_scraper.middleware.retry as _retry

sys.modules[__name__] = _retry

"""Backward-compatible shim for the Web Scraper rate limiter middleware module."""

import sys

import plugins.web_scraper.middleware.rate_limiter as _rate_limiter

sys.modules[__name__] = _rate_limiter

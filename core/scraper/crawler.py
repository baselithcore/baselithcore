"""Backward-compatible shim for the Web Scraper crawler module."""

import sys

import plugins.web_scraper.crawler as _crawler

sys.modules[__name__] = _crawler

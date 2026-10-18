"""Backward-compatible shim for the Web Scraper crawler module."""

import sys

import baselith.plugins.web_scraper.engine.crawler as _crawler

sys.modules[__name__] = _crawler

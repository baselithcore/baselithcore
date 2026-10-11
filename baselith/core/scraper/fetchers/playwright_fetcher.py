"""Backward-compatible shim for the Web Scraper Playwright fetcher module."""

import sys

import baselith.plugins.web_scraper.fetchers.playwright_fetcher as _playwright_fetcher

sys.modules[__name__] = _playwright_fetcher

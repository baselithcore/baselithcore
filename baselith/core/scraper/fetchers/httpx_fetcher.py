"""Backward-compatible shim for the Web Scraper httpx fetcher module."""

import sys

import baselith.plugins.web_scraper.fetchers.httpx_fetcher as _httpx_fetcher

sys.modules[__name__] = _httpx_fetcher

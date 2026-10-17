# baselith/core/scraper/fetchers/__init__.py
"""Fetcher implementations for the web scraper module."""

from .base import BaseFetcher, FetchError
from .httpx_fetcher import HttpxFetcher
from .playwright_fetcher import PlaywrightFetcher
from .scrapling_fetcher import ScraplingFetcher, scrapling_available

__all__ = [
    "BaseFetcher",
    "FetchError",
    "HttpxFetcher",
    "PlaywrightFetcher",
    "ScraplingFetcher",
    "scrapling_available",
]

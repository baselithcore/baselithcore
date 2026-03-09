"""Scraper configuration.

Configuration for the web scraper module, including fetcher, crawler, and rate limiting settings.
"""

from __future__ import annotations

import logging
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

logger = logging.getLogger(__name__)


class ScraperConfig(BaseSettings):
    """Configuration for the web scraper module.

    All settings can be overridden via environment variables with SCRAPER_ prefix.
    """

    model_config = SettingsConfigDict(
        env_prefix="SCRAPER_",
        env_file=".env",
        extra="ignore",
    )

    # Fetcher settings
    default_fetcher: Literal["httpx", "playwright"] = Field(
        default="httpx",
        description="Default fetcher to use (httpx for speed, playwright for JS)",
    )
    user_agent: str = Field(
        default="Mozilla/5.0 (compatible; MultiAgentBot/1.0)",
        description="User agent string for requests",
    )
    timeout_seconds: float = Field(
        default=30.0,
        description="Request timeout in seconds",
        ge=1.0,
        le=300.0,
    )
    max_connections: int = Field(
        default=10,
        description="Maximum concurrent connections",
        ge=1,
        le=100,
    )

    # Rate limiting
    rate_limit_enabled: bool = Field(
        default=True,
        description="Enable rate limiting",
    )
    rate_limit_requests: int = Field(
        default=10,
        description="Maximum requests per period",
        ge=1,
    )
    rate_limit_period_seconds: float = Field(
        default=1.0,
        description="Rate limit period in seconds",
        ge=0.1,
    )
    rate_limit_per_domain: bool = Field(
        default=True,
        description="Apply rate limits per domain instead of globally",
    )

    # Crawler settings
    max_depth: int = Field(
        default=3,
        description="Maximum crawl depth",
        ge=1,
        le=10,
    )
    max_pages: int = Field(
        default=100,
        description="Maximum pages to crawl",
        ge=1,
        le=10000,
    )
    follow_robots_txt: bool = Field(
        default=True,
        description="Respect robots.txt directives",
    )
    respect_nofollow: bool = Field(
        default=True,
        description="Respect nofollow link attributes",
    )

    # Retry settings
    max_retries: int = Field(
        default=3,
        description="Maximum retry attempts",
        ge=0,
        le=10,
    )
    retry_backoff_factor: float = Field(
        default=0.5,
        description="Exponential backoff factor for retries",
        ge=0.1,
    )

    # Caching
    cache_enabled: bool = Field(
        default=True,
        description="Enable response caching",
    )
    cache_ttl_seconds: int = Field(
        default=3600,
        description="Cache TTL in seconds",
        ge=0,
    )
    cache_backend: Literal["memory", "redis"] = Field(
        default="memory",
        description="Cache backend to use",
    )

    # Security
    block_private_ips: bool = Field(
        default=True,
        description="Block requests to private/internal IPs (SSRF protection)",
    )
    blocked_extensions: list[str] = Field(
        default_factory=lambda: [
            ".exe",
            ".zip",
            ".tar",
            ".gz",
            ".rar",
            ".7z",
            ".pdf",
            ".doc",
            ".docx",
            ".xls",
            ".xlsx",
            ".mp3",
            ".mp4",
            ".avi",
            ".mov",
            ".wmv",
            ".jpg",
            ".jpeg",
            ".png",
            ".gif",
            ".svg",
            ".ico",
        ],
        description="File extensions to skip during crawling",
    )

    # Playwright-specific settings
    playwright_headless: bool = Field(
        default=True,
        description="Run Playwright in headless mode",
    )
    playwright_wait_until: Literal["load", "domcontentloaded", "networkidle"] = Field(
        default="networkidle",
        description="Playwright navigation wait condition",
    )
    playwright_screenshot: bool = Field(
        default=False,
        description="Take screenshots with Playwright",
    )

    # Logging
    log_requests: bool = Field(
        default=True,
        description="Log individual requests",
    )
    log_level: str = Field(
        default="INFO",
        description="Logging level for scraper",
    )


# Global instance
_scraper_config: ScraperConfig | None = None


def get_scraper_config() -> ScraperConfig:
    """Get or create the global scraper configuration instance."""
    global _scraper_config
    if _scraper_config is None:
        _scraper_config = ScraperConfig()
        logger.debug("Initialized ScraperConfig")
    return _scraper_config

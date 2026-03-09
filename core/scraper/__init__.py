# core/scraper/__init__.py
"""Web Scraper and Crawler Module.

This module provides a complete, generic web scraping and crawling solution
with the following features:

- **Dual-mode Fetching**: HttpxFetcher (fast HTTP) and PlaywrightFetcher (JS rendering)
- **Modular Extractors**: Text, links, images, metadata, Schema.org, CSS selectors
- **Middleware Chain**: Rate limiting, caching, retry logic, logging
- **Recursive Crawling**: BFS/DFS traversal with depth/page limits
- **Storage Backends**: Memory and filesystem storage
- **SSRF Protection**: Built-in protection against server-side request forgery
- **robots.txt Compliance**: Automatic parsing and respect for robots.txt

Example usage:
    ```python
    from core.scraper import Scraper, CrawlEngine

    # Simple scraping
    async with Scraper() as scraper:
        page, data = await scraper.scrape("https://example.com")
        print(data.text, data.links)

    # Crawling
    crawler = CrawlEngine(max_pages=10, max_depth=2)
    async for page, data in crawler.crawl("https://example.com"):
        print(page.url, len(data.text or ""))
    ```
"""

# Configuration
from core.config.scraper import ScraperConfig, get_scraper_config

# Models
from .models import (
    CrawlError,
    CrawlResult,
    CrawlStats,
    ExtractedData,
    Image,
    Link,
    PageMetadata,
    ScrapedPage,
)

# Fetchers
from .fetchers import BaseFetcher, FetchError, HttpxFetcher, PlaywrightFetcher

# Extractors
from .extractors import (
    BaseExtractor,
    CssSelectorExtractor,
    ExtractorRegistry,
    ExtractionSchema,
    FieldSchema,
    ImageExtractor,
    LinkExtractor,
    MetadataExtractor,
    SchemaOrgExtractor,
    TextExtractor,
)

# Middleware
from .middleware import (
    BaseMiddleware,
    CacheMiddleware,
    LoggingMiddleware,
    MiddlewareChain,
    RateLimiterMiddleware,
    RetryMiddleware,
)

# Storage
from .storage import BaseStorage, FilesystemStorage, MemoryStorage

# Main facades
from .scraper import Scraper
from .crawler import CrawlEngine, create_crawler

# Utilities
from .utils import (
    check_ssrf_safe,
    clean_text,
    extract_domain,
    is_blocked_extension,
    is_private_ip,
    is_same_domain,
    is_url_allowed_by_robots,
    is_valid_url,
    normalize_url,
    parse_robots_txt,
)

__all__ = [
    # Config
    "ScraperConfig",
    "get_scraper_config",
    # Models
    "ScrapedPage",
    "ExtractedData",
    "Link",
    "Image",
    "PageMetadata",
    "CrawlResult",
    "CrawlStats",
    "CrawlError",
    # Fetchers
    "BaseFetcher",
    "FetchError",
    "HttpxFetcher",
    "PlaywrightFetcher",
    # Extractors
    "BaseExtractor",
    "ExtractorRegistry",
    "TextExtractor",
    "LinkExtractor",
    "ImageExtractor",
    "MetadataExtractor",
    "SchemaOrgExtractor",
    "CssSelectorExtractor",
    "ExtractionSchema",
    "FieldSchema",
    # Middleware
    "BaseMiddleware",
    "MiddlewareChain",
    "RateLimiterMiddleware",
    "CacheMiddleware",
    "RetryMiddleware",
    "LoggingMiddleware",
    # Storage
    "BaseStorage",
    "MemoryStorage",
    "FilesystemStorage",
    # Facades
    "Scraper",
    "CrawlEngine",
    "create_crawler",
    # Utils
    "normalize_url",
    "extract_domain",
    "is_same_domain",
    "is_valid_url",
    "is_private_ip",
    "check_ssrf_safe",
    "is_blocked_extension",
    "clean_text",
    "parse_robots_txt",
    "is_url_allowed_by_robots",
]

"""Web Scraper plugin package.

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
    from baselith.plugins.web_scraper import Scraper, CrawlEngine

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
from baselith.core.config.scraper import ScraperConfig, get_scraper_config
from baselith.core.plugins.compat_aliases import register_module_aliases

from .engine.crawler import CrawlEngine, create_crawler

# Main facades
from .engine.scraper import Scraper

# Extractors
from .extractors import (
    BaseExtractor,
    CssSelectorExtractor,
    ExtractionSchema,
    ExtractorRegistry,
    FieldSchema,
    ImageExtractor,
    LinkExtractor,
    MarkdownExtractor,
    MetadataExtractor,
    SchemaOrgExtractor,
    TextExtractor,
)

# Fetchers
from .fetchers import (
    BaseFetcher,
    FetchError,
    HttpxFetcher,
    PlaywrightFetcher,
    ScraplingFetcher,
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

# Storage
from .storage import BaseStorage, FilesystemStorage, MemoryStorage

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
    "ScraplingFetcher",
    # Extractors
    "BaseExtractor",
    "ExtractorRegistry",
    "TextExtractor",
    "LinkExtractor",
    "ImageExtractor",
    "MetadataExtractor",
    "SchemaOrgExtractor",
    "CssSelectorExtractor",
    "MarkdownExtractor",
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

from .plugin import WebScraperPlugin as WebScraperPlugin

__all__.append("WebScraperPlugin")

# The modules below moved into sub-packages (plugin layout standard); the old
# import paths stay importable so documented code and downstream plugins keep working.
register_module_aliases(
    {
        "crawler": "engine.crawler",
        "scraper": "engine.scraper",
        "protocols": "engine.protocols",
        "_http_pool": "engine.http_pool",
    },
    packages=(__name__, "baselith.core.scraper"),
    unchanged=(
        "models",
        "utils",
        "extractors",
        "fetchers",
        "middleware",
        "storage",
        "tools",
        "plugin",
    ),
)

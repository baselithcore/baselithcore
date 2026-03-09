# Web Scraper Module

A complete and generic module for web scraping and crawling, designed to be used standalone or integrated with the baselith-core.

## Features

- **Dual-mode Fetching**: Fast HTTP (httpx) or JavaScript rendering (Playwright)
- **7 Modular Extractors**: Text, Links, Images, Metadata, Schema.org, CSS Selectors
- **Middleware Chain**: Rate limiting, Caching, Retry, Logging
- **Recursive Crawler**: BFS with depth/page limits and robots.txt support
- **Storage Backends**: Memory, Filesystem
- **Agent Tools**: Ready-to-use functions for agents

## Quick Start

### Single Page Scraping

```python
from core.scraper import Scraper

async def main():
    async with Scraper() as scraper:
        page, data = await scraper.scrape("https://example.com")
        
        print(f"Title: {data.metadata.title}")
        print(f"Text length: {len(data.text or '')}")
        print(f"Links found: {len(data.links)}")
```

### With JavaScript Rendering

```python
# For Single Page Applications (React, Vue, etc.)
page, data = await scraper.scrape(
    "https://spa-example.com",
    use_js=True,
)
```

### Extractor Selection

```python
# Text and links only
page, data = await scraper.scrape(
    "https://example.com",
    extractors=["text", "links"],
)

# With structured data
page, data = await scraper.scrape(
    "https://recipe-site.com",
    extractors=["text", "metadata", "schema_org"],
)
```

### Multi-Page Crawling

```python
from core.scraper import CrawlEngine

async def main():
    async with CrawlEngine(max_pages=20, max_depth=2) as crawler:
        async for page, data in crawler.crawl("https://example.com"):
            print(f"Crawled: {page.url}")
            print(f"  Text: {len(data.text or '')} chars")
            print(f"  Links: {len(data.links)}")
```

### Complete Result

```python
result = await crawler.crawl_all("https://example.com")

print(f"Pages crawled: {result.stats.pages_crawled}")
print(f"Total time: {result.stats.duration_seconds:.1f}s")
print(f"All text: {result.get_all_text()[:500]}")
```

## Extractors

| Extractor      | Output             | Description                         |
| -------------- | ------------------ | ----------------------------------- |
| `text`         | `(str, list[str])` | Clean text and text blocks          |
| `links`        | `list[Link]`       | URL, anchor text, internal/external |
| `images`       | `list[Image]`      | src, alt, dimensions, lazy loading  |
| `metadata`     | `PageMetadata`     | Title, OG, Twitter, canonical       |
| `schema_org`   | `list[dict]`       | JSON-LD and Microdata               |
| `css_selector` | `dict[str, Any]`   | Custom extraction via CSS           |

### Custom Extraction with CSS

```python
from core.scraper.extractors import CssSelectorExtractor, create_schema

# Define a schema
schema = create_schema("product", {
    "title": "h1.product-title",
    "price": {"selector": ".price", "transform": "float"},
    "images": {"selector": "img.gallery", "attribute": "src", "multiple": True},
})

extractor = CssSelectorExtractor(schema)
result = extractor.extract_with_schema(page, schema)
print(result)  # {"title": "...", "price": 29.99, "images": [...]}
```

## Configuration

All options are configurable via environment variables with the `SCRAPER_` prefix:

```env
# Fetcher
SCRAPER_DEFAULT_FETCHER=httpx           # httpx or playwright
SCRAPER_USER_AGENT="Mozilla/5.0 ..."
SCRAPER_TIMEOUT_SECONDS=30

# Rate Limiting
SCRAPER_RATE_LIMIT_ENABLED=true
SCRAPER_RATE_LIMIT_REQUESTS=10
SCRAPER_RATE_LIMIT_PERIOD_SECONDS=1.0
SCRAPER_RATE_LIMIT_PER_DOMAIN=true

# Crawler
SCRAPER_MAX_DEPTH=3
SCRAPER_MAX_PAGES=100
SCRAPER_FOLLOW_ROBOTS_TXT=true

# Caching
SCRAPER_CACHE_ENABLED=true
SCRAPER_CACHE_TTL_SECONDS=3600
SCRAPER_CACHE_BACKEND=memory            # memory or redis

# Security
SCRAPER_BLOCK_PRIVATE_IPS=true          # SSRF protection
```

## Agent Tools

The module includes ready-to-use functions for agent integration:

```python
from core.scraper.tools import web_scrape, web_crawl, extract_structured_data

# Scrape single page
result = await web_scrape("https://example.com")
print(result["text"])
print(result["links"])

# Crawl site
result = await web_crawl("https://example.com", max_pages=10)
for page in result["pages"]:
    print(page["url"], page["title"])

# Extract structured data
result = await extract_structured_data("https://recipe.com")
print(result["schema_org"])
```

## Middleware

### Rate Limiter

Token bucket rate limiting per domain:

```python
from core.scraper.middleware import RateLimiterMiddleware

limiter = RateLimiterMiddleware(
    requests_per_second=5.0,
    burst_capacity=10,
    per_domain=True,
)
```

### Cache

LRU cache with optional Redis support:

```python
from core.scraper.middleware import CacheMiddleware

cache = CacheMiddleware(
    ttl_seconds=3600,
    max_size=1000,
    use_redis=True,
    redis_url="redis://localhost:6379",
)
```

### Retry

Exponential backoff with jitter:

```python
from core.scraper.middleware import RetryMiddleware

retry = RetryMiddleware(
    max_retries=3,
    backoff_factor=0.5,
    retry_status_codes={429, 500, 502, 503, 504},
)
```

## Storage

### Memory Storage

```python
from core.scraper.storage import MemoryStorage

storage = MemoryStorage(max_size=10000)
```

### Filesystem Storage

```python
from core.scraper.storage import FilesystemStorage

storage = FilesystemStorage("./scraped_data")
```

## Extensibility

### Custom Extractor

```python
from core.scraper.extractors import BaseExtractor, ExtractorRegistry

@ExtractorRegistry.register
class MyExtractor(BaseExtractor):
    @property
    def name(self) -> str:
        return "my_extractor"
    
    def extract(self, page):
        soup = self.get_soup(page)
        # ... custom extraction logic
        return result
```

### Custom Middleware

```python
from core.scraper.middleware import BaseMiddleware

class MyMiddleware(BaseMiddleware):
    @property
    def name(self) -> str:
        return "my_middleware"
    
    async def process(self, url, next_fn):
        # Pre-processing
        result = await next_fn(url)
        # Post-processing
        return result
```

## Security

- **SSRF Protection**: Automatic blocking of private/internal IPs
- **robots.txt**: Respect for directives (configurable)
- **Rate Limiting**: Protection against overload
- **Extension Filtering**: Automatic filtering of binary files

## Dependencies

- `httpx` - HTTP client
- `beautifulsoup4` - HTML parsing
- `playwright` (optional) - JavaScript rendering

## Module Structure

```text
core/scraper/
├── __init__.py          # Public API
├── config.py            # Pydantic configuration
├── models.py            # Data classes
├── protocols.py         # Abstract interfaces
├── scraper.py           # Main Scraper facade
├── crawler.py           # CrawlEngine
├── tools.py             # Agent tools
├── utils.py             # Utilities
├── fetchers/
│   ├── base.py          # BaseFetcher
│   ├── httpx_fetcher.py # HTTP fetcher
│   └── playwright_fetcher.py  # JS fetcher
├── extractors/
│   ├── base.py          # BaseExtractor, Registry
│   ├── text.py          # TextExtractor
│   ├── links.py         # LinkExtractor
│   ├── images.py        # ImageExtractor
│   ├── metadata.py      # MetadataExtractor
│   ├── schema_org.py    # SchemaOrgExtractor
│   └── css_selector.py  # CssSelectorExtractor
├── middleware/
│   ├── base.py          # MiddlewareChain
│   ├── rate_limiter.py  # RateLimiterMiddleware
│   ├── cache.py         # CacheMiddleware
│   ├── retry.py         # RetryMiddleware
│   └── logging.py       # LoggingMiddleware
└── storage/
    ├── base.py          # BaseStorage
    ├── memory.py        # MemoryStorage
    └── filesystem.py    # FilesystemStorage
```

# core/scraper/tools.py
"""Agent tools for web scraping and crawling.

These tools provide a simple interface for agents to interact with
the web scraper module.
"""

from __future__ import annotations

from typing import Any

from .crawler import CrawlEngine
from .scraper import Scraper


async def web_scrape(
    url: str,
    extract: list[str] | None = None,
    use_javascript: bool = False,
) -> dict[str, Any]:
    """Scrape a single web page and extract data.

    This tool allows agents to fetch and extract content from web pages.

    Args:
        url: The URL to scrape.
        extract: Types of data to extract. Options: 'text', 'links', 'images',
                 'metadata', 'schema_org'. Default: ['text', 'links', 'metadata'].
        use_javascript: Whether to render JavaScript (slower but needed for SPAs).

    Returns:
        Dictionary containing:
        - url: The final URL after redirects
        - status_code: HTTP status code
        - success: Whether the scrape was successful
        - error: Error message if failed
        - text: Extracted text content (if 'text' in extract)
        - links: List of extracted links (if 'links' in extract)
        - images: List of extracted images (if 'images' in extract)
        - metadata: Page metadata (if 'metadata' in extract)
        - schema_org: Structured data (if 'schema_org' in extract)
    """
    extract = extract or ["text", "links", "metadata"]

    async with Scraper() as scraper:
        page, data = await scraper.scrape(
            url=url,
            extractors=extract,
            use_js=use_javascript,
        )

    result: dict[str, Any] = {
        "url": page.final_url,
        "status_code": page.status_code,
        "success": page.is_success,
        "error": page.error,
    }

    # Add extracted data based on what was requested
    if "text" in extract:
        result["text"] = data.text

    if "links" in extract:
        result["links"] = [
            {"url": link.url, "text": link.text, "is_internal": link.is_internal}
            for link in data.links[:50]  # Limit to 50 links
        ]

    if "images" in extract:
        result["images"] = [
            {"src": img.src, "alt": img.alt}
            for img in data.images[:20]  # Limit to 20 images
        ]

    if "metadata" in extract and data.metadata:
        result["metadata"] = {
            "title": data.metadata.title,
            "description": data.metadata.description,
            "keywords": data.metadata.keywords,
            "og_title": data.metadata.og_title,
            "og_description": data.metadata.og_description,
            "og_image": data.metadata.og_image,
        }

    if "schema_org" in extract:
        result["schema_org"] = data.schema_org[:10]  # Limit to 10 schemas

    return result


async def web_crawl(
    seed_url: str,
    max_pages: int = 10,
    max_depth: int = 2,
    extract: list[str] | None = None,
    use_javascript: bool = False,
) -> dict[str, Any]:
    """Crawl a website starting from a seed URL.

    This tool allows agents to discover and extract content from multiple
    pages on a website.

    Args:
        seed_url: The starting URL for the crawl.
        max_pages: Maximum number of pages to crawl (1-100).
        max_depth: Maximum depth to follow links (1-5).
        extract: Types of data to extract. Default: ['text'].
        use_javascript: Whether to render JavaScript.

    Returns:
        Dictionary containing:
        - seed_url: The starting URL
        - pages_crawled: Number of pages successfully crawled
        - pages_failed: Number of pages that failed
        - duration_seconds: Total crawl duration
        - pages: List of crawled pages with extracted data
    """
    # Enforce limits
    max_pages = min(max(1, max_pages), 100)
    max_depth = min(max(1, max_depth), 5)
    extract = extract or ["text"]

    crawler = CrawlEngine(
        max_pages=max_pages,
        max_depth=max_depth,
        extractors=extract,
    )

    result = await crawler.crawl_full(seed_url, use_js=use_javascript)

    pages_data = []
    for page in result.pages[:max_pages]:
        page_data: dict[str, Any] = {
            "url": page.final_url,
            "status_code": page.status_code,
            "success": page.is_success,
        }

        extracted = result.extracted.get(page.url)
        if extracted:
            if "text" in extract:
                # Truncate text for agent consumption
                text = extracted.text or ""
                page_data["text"] = text[:2000] + "..." if len(text) > 2000 else text

            if "links" in extract:
                page_data["links_count"] = len(extracted.links)

            if "metadata" in extract and extracted.metadata:
                page_data["title"] = extracted.metadata.title

        pages_data.append(page_data)

    return {
        "seed_url": result.seed_url,
        "pages_crawled": result.stats.pages_crawled,
        "pages_failed": result.stats.pages_failed,
        "duration_seconds": result.stats.duration_seconds,
        "pages": pages_data,
    }


async def extract_structured_data(
    url: str,
    selectors: dict[str, str] | None = None,
    use_javascript: bool = False,
) -> dict[str, Any]:
    """Extract structured data from a web page using CSS selectors or Schema.org.

    This tool allows agents to extract specific fields from web pages
    using CSS selectors or by parsing Schema.org structured data.

    Args:
        url: The URL to scrape.
        selectors: Optional dict mapping field names to CSS selectors.
                   Example: {"title": "h1", "price": ".price"}
        use_javascript: Whether to render JavaScript.

    Returns:
        Dictionary containing:
        - url: The final URL
        - success: Whether extraction was successful
        - custom: Fields extracted via CSS selectors
        - schema_org: Schema.org structured data found on the page
    """
    from .extractors import CssSelectorExtractor, ExtractionSchema

    extractors = ["schema_org"]

    async with Scraper() as scraper:
        # If selectors provided, configure CSS extractor
        if selectors:
            schema = ExtractionSchema()
            for name, selector in selectors.items():
                schema.add_field(name=name, selector=selector)

            css_extractor = CssSelectorExtractor(schema=schema)
            scraper._extractors["css_selector"] = css_extractor
            extractors.append("css_selector")

        page, data = await scraper.scrape(
            url=url,
            extractors=extractors,
            use_js=use_javascript,
        )

    return {
        "url": page.final_url,
        "success": page.is_success,
        "error": page.error,
        "custom": data.custom if selectors else {},
        "schema_org": data.schema_org[:10],  # Limit
    }


# Tool metadata for agent registration
SCRAPER_TOOLS = [
    {
        "name": "web_scrape",
        "function": web_scrape,
        "description": "Scrape a single web page and extract text, links, images, and metadata.",
        "parameters": {
            "type": "object",
            "properties": {
                "url": {
                    "type": "string",
                    "description": "The URL to scrape",
                },
                "extract": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Types of data to extract: text, links, images, metadata, schema_org",
                },
                "use_javascript": {
                    "type": "boolean",
                    "description": "Whether to render JavaScript (needed for SPAs)",
                },
            },
            "required": ["url"],
        },
    },
    {
        "name": "web_crawl",
        "function": web_crawl,
        "description": "Crawl a website starting from a seed URL, discovering and extracting content from multiple pages.",
        "parameters": {
            "type": "object",
            "properties": {
                "seed_url": {
                    "type": "string",
                    "description": "The starting URL for the crawl",
                },
                "max_pages": {
                    "type": "integer",
                    "description": "Maximum number of pages to crawl (1-100)",
                },
                "max_depth": {
                    "type": "integer",
                    "description": "Maximum depth to follow links (1-5)",
                },
                "extract": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Types of data to extract",
                },
                "use_javascript": {
                    "type": "boolean",
                    "description": "Whether to render JavaScript",
                },
            },
            "required": ["seed_url"],
        },
    },
    {
        "name": "extract_structured_data",
        "function": extract_structured_data,
        "description": "Extract structured data from a web page using CSS selectors or Schema.org.",
        "parameters": {
            "type": "object",
            "properties": {
                "url": {
                    "type": "string",
                    "description": "The URL to scrape",
                },
                "selectors": {
                    "type": "object",
                    "description": "Dict mapping field names to CSS selectors",
                },
                "use_javascript": {
                    "type": "boolean",
                    "description": "Whether to render JavaScript",
                },
            },
            "required": ["url"],
        },
    },
]

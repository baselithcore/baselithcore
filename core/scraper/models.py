# core/scraper/models.py
"""Data models for the web scraper module.

This module defines immutable dataclasses for representing scraped pages,
extracted data, links, images, and crawl results.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any


@dataclass(frozen=True)
class Link:
    """Represents an extracted link from a web page."""

    url: str
    text: str
    rel: str | None = None
    is_internal: bool = True
    nofollow: bool = False


@dataclass(frozen=True)
class Image:
    """Represents an extracted image from a web page."""

    src: str
    alt: str = ""
    width: int | None = None
    height: int | None = None
    srcset: str | None = None


@dataclass(frozen=True)
class PageMetadata:
    """Metadata extracted from a web page."""

    title: str | None = None
    description: str | None = None
    keywords: list[str] = field(default_factory=list)
    author: str | None = None
    canonical_url: str | None = None
    og_title: str | None = None
    og_description: str | None = None
    og_image: str | None = None
    og_type: str | None = None
    twitter_card: str | None = None
    twitter_title: str | None = None
    twitter_description: str | None = None
    twitter_image: str | None = None
    robots: str | None = None
    language: str | None = None


@dataclass(frozen=True)
class ScrapedPage:
    """Represents a fetched web page with its raw content."""

    url: str
    final_url: str
    status_code: int
    html: str
    headers: dict[str, str] = field(default_factory=dict)
    fetched_at: datetime = field(default_factory=datetime.now)
    fetch_time_ms: float = 0.0
    error: str | None = None

    @property
    def is_success(self) -> bool:
        """Check if the page was fetched successfully."""
        return 200 <= self.status_code < 400 and self.error is None

    @property
    def content_type(self) -> str:
        """Get the content type from headers."""
        return self.headers.get("content-type", "").split(";")[0].strip()


@dataclass
class ExtractedData:
    """Container for all extracted data from a page."""

    text: str | None = None
    links: list[Link] = field(default_factory=list)
    images: list[Image] = field(default_factory=list)
    metadata: PageMetadata | None = None
    schema_org: list[dict[str, Any]] = field(default_factory=list)
    custom: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for serialization."""
        return {
            "text": self.text,
            "links": [
                {
                    "url": link.url,
                    "text": link.text,
                    "is_internal": link.is_internal,
                }
                for link in self.links
            ],
            "images": [{"src": i.src, "alt": i.alt} for i in self.images],
            "metadata": (
                {
                    "title": self.metadata.title,
                    "description": self.metadata.description,
                }
                if self.metadata
                else None
            ),
            "schema_org": self.schema_org,
            "custom": self.custom,
        }


@dataclass(frozen=True)
class CrawlError:
    """Represents an error during crawling."""

    url: str
    error_type: str
    message: str
    timestamp: datetime = field(default_factory=datetime.now)


@dataclass
class CrawlStats:
    """Statistics for a crawl session."""

    pages_crawled: int = 0
    pages_failed: int = 0
    total_bytes: int = 0
    start_time: datetime = field(default_factory=datetime.now)
    end_time: datetime | None = None
    avg_response_time_ms: float = 0.0

    @property
    def duration_seconds(self) -> float:
        """Get total duration in seconds."""
        end = self.end_time or datetime.now()
        return (end - self.start_time).total_seconds()


@dataclass
class CrawlResult:
    """Result of a complete crawl session."""

    seed_url: str
    pages: list[ScrapedPage] = field(default_factory=list)
    extracted: dict[str, ExtractedData] = field(default_factory=dict)
    stats: CrawlStats = field(default_factory=CrawlStats)
    errors: list[CrawlError] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for serialization."""
        return {
            "seed_url": self.seed_url,
            "pages_count": len(self.pages),
            "errors_count": len(self.errors),
            "stats": {
                "pages_crawled": self.stats.pages_crawled,
                "pages_failed": self.stats.pages_failed,
                "duration_seconds": self.stats.duration_seconds,
            },
        }

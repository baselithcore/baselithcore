"""Constants and configuration for web document source.

Contains HTML parsing constants, security filters, and configuration
values used by WebDocumentSource.
"""

from __future__ import annotations

from typing import FrozenSet, Tuple

# HTML tags to remove during parsing
DROP_TAGS: FrozenSet[str] = frozenset(
    {
        "script",
        "style",
        "noscript",
        "iframe",
        "canvas",
        "svg",
        "form",
        "header",
        "footer",
        "nav",
        "aside",
        "video",
        "audio",
    }
)

# CSS selectors for finding main content
MAIN_SELECTORS: Tuple[str, ...] = (
    "main",
    "article",
    "section[role='main']",
    "div[id*='content']",
    "div[class*='content']",
    "div[id*='article']",
    "div[class*='article']",
    "div[data-content]",
)

# HTML block-level tags for text extraction
BLOCK_TAGS: Tuple[str, ...] = (
    "h1",
    "h2",
    "h3",
    "h4",
    "h5",
    "h6",
    "p",
    "li",
    "blockquote",
    "pre",
    "code",
)

# Heading tags for special handling
HEADING_TAGS: FrozenSet[str] = frozenset({"h1", "h2", "h3", "h4", "h5", "h6"})

# Binary file extensions to skip
BINARY_EXTENSIONS: FrozenSet[str] = frozenset(
    {
        ".jpg",
        ".jpeg",
        ".png",
        ".gif",
        ".webp",
        ".svg",
        ".ico",
        ".zip",
        ".pdf",
        ".rar",
        ".tar",
        ".gz",
        ".tgz",
        ".mp3",
        ".mp4",
        ".mov",
        ".ppt",
        ".pptx",
        ".doc",
        ".docx",
        ".xls",
        ".xlsx",
    }
)

# Private IP prefixes for SSRF protection
PRIVATE_IP_PREFIXES: Tuple[str, ...] = (
    "10.",
    "127.",
    "169.254.",
    "172.16.",
    "172.17.",
    "172.18.",
    "172.19.",
    "172.20.",
    "172.21.",
    "172.22.",
    "172.23.",
    "172.24.",
    "172.25.",
    "172.26.",
    "172.27.",
    "172.28.",
    "172.29.",
    "172.30.",
    "172.31.",
    "192.168.",
)

# Content size thresholds
MIN_BLOCK_CHARS: int = 24
MIN_HEADING_CHARS: int = 6
MIN_DOCUMENT_CHARS: int = 300

# Crawling limits
MAX_LINKS_PER_PAGE: int = 25

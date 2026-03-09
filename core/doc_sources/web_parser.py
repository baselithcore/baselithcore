"""HTML parsing utilities for web document source.

Contains functions for parsing HTML content, extracting text blocks,
and collecting links from web pages.
"""

from __future__ import annotations

import re
from typing import List, Optional, Tuple
from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup

from .web_constants import (
    DROP_TAGS,
    MAIN_SELECTORS,
    BLOCK_TAGS,
    HEADING_TAGS,
    MIN_BLOCK_CHARS,
    MIN_HEADING_CHARS,
    MAX_LINKS_PER_PAGE,
)
from .utils import normalize_text


def clean_block(text: str, min_chars: int) -> Optional[str]:
    """Clean a text block and check minimum length.

    Args:
        text: Raw text to clean
        min_chars: Minimum character count

    Returns:
        Cleaned text or None if too short
    """
    cleaned = re.sub(r"\s+", " ", text or "").strip()
    if len(cleaned) < min_chars:
        return None
    return cleaned


def select_main_node(soup: BeautifulSoup):
    """Find the main content node in a parsed HTML document.

    Uses heuristics to find the node with the most textual content.

    Args:
        soup: Parsed BeautifulSoup document

    Returns:
        The best candidate node for main content
    """
    best_node = None
    best_score = 0
    for selector in MAIN_SELECTORS:
        candidate = soup.select_one(selector)
        if not candidate:
            continue
        score = len(candidate.get_text(" ", strip=True))
        if score > best_score:
            best_node = candidate
            best_score = score
    if best_node is None:
        best_node = soup.body or soup
    return best_node


def collect_blocks(node) -> List[str]:
    """Extract text blocks from an HTML node.

    Args:
        node: BeautifulSoup node to extract from

    Returns:
        List of cleaned text blocks
    """
    blocks: List[str] = []
    for element in node.find_all(BLOCK_TAGS):
        raw = element.get_text(" ", strip=True)
        if not raw:
            continue
        minimum = MIN_HEADING_CHARS if element.name in HEADING_TAGS else MIN_BLOCK_CHARS
        cleaned = clean_block(raw, minimum)
        if cleaned:
            blocks.append(cleaned)
    if not blocks:
        fallback = clean_block(node.get_text(" ", strip=True), MIN_BLOCK_CHARS)
        if fallback:
            blocks.append(fallback)
    return blocks


def collect_links(
    soup: BeautifulSoup,
    current_url: str,
    allowed_domain: str,
    normalize_domain_fn,
    should_skip_fn,
    normalize_parsed_fn,
) -> List[str]:
    """Collect valid links from a parsed HTML document.

    Args:
        soup: Parsed BeautifulSoup document
        current_url: Current page URL for resolving relative links
        allowed_domain: Domain to restrict links to
        normalize_domain_fn: Function to normalize domain names
        should_skip_fn: Function to check if URL should be skipped
        normalize_parsed_fn: Function to normalize parsed URLs

    Returns:
        List of normalized absolute URLs
    """
    collected: List[str] = []
    seen: set[str] = set()

    for anchor in soup.find_all("a", href=True):
        href_raw = anchor.get("href")
        href = str(href_raw).strip() if href_raw else ""
        if not href or href.startswith("#"):
            continue
        absolute = urljoin(current_url, href)
        parsed = urlparse(absolute)
        if parsed.scheme not in {"http", "https"}:
            continue
        if not parsed.netloc:
            continue
        if normalize_domain_fn(parsed.netloc) != allowed_domain:
            continue
        normalized = normalize_parsed_fn(parsed)
        if not normalized or normalized in seen:
            continue
        if should_skip_fn(normalized):
            continue
        collected.append(normalized)
        seen.add(normalized)
        if len(collected) >= MAX_LINKS_PER_PAGE:
            break
    return collected


def parse_page(
    html: str,
    current_url: str,
    allowed_domain: str,
    normalize_domain_fn,
    should_skip_fn,
    normalize_parsed_fn,
) -> Optional[Tuple[str, str, Optional[str], List[str]]]:
    """Parse an HTML page and extract text content and links.

    Args:
        html: Raw HTML content
        current_url: Current page URL
        allowed_domain: Domain to restrict links to
        normalize_domain_fn: Function to normalize domain names
        should_skip_fn: Function to check if URL should be skipped
        normalize_parsed_fn: Function to normalize parsed URLs

    Returns:
        Tuple of (text, title, language, links) or None if parsing fails
    """
    soup = BeautifulSoup(html, "html.parser")

    # Remove unwanted tags
    for tag in DROP_TAGS:
        for node in soup.find_all(tag):
            node.decompose()
    for node in soup.find_all(attrs={"aria-hidden": "true"}):
        node.decompose()
    for node in soup.find_all(attrs={"role": "navigation"}):
        node.decompose()

    # Collect links before further processing
    links = collect_links(
        soup,
        current_url,
        allowed_domain,
        normalize_domain_fn,
        should_skip_fn,
        normalize_parsed_fn,
    )

    # Extract metadata
    title_node = soup.find("title")
    title = title_node.get_text(" ", strip=True) if title_node else ""
    html_tag = soup.find("html")
    lang = html_tag.get("lang") if html_tag else None

    # Extract main content
    main = select_main_node(soup)
    blocks = collect_blocks(main)
    if not blocks:
        return None

    text = "\n\n".join(blocks)
    text = normalize_text(text)
    if not text:
        return None

    # Cast lang to str if it's an AttributeValueList
    lang_val = str(lang) if lang and not isinstance(lang, str) else lang
    return text, title, lang_val, links[:MAX_LINKS_PER_PAGE]  # type: ignore[return-value]

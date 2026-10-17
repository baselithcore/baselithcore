"""A page as clean Markdown, through Scrapling's converter: the form a language model reads best.

Scrapling removes scripts, styles and content hidden with CSS (``display:none`` and the like)
before it converts. This also drops what the HTML itself marks hidden or inert (the ``hidden``
attribute, ``aria-hidden="true"``, ``<template>``, ``<noscript>``), so text a reader never sees
cannot reach a model as if it were the page.

Optional: it needs ``scrapling`` and ``markdownify``; without them the extractor yields nothing
and the other extractors are unaffected.
"""

from __future__ import annotations

import importlib.util
import logging
from typing import Any

from ..models import ScrapedPage
from .base import BaseExtractor

INSTALL_HINT = (
    'markdown extraction needs: pip install "scrapling[fetchers]" markdownify'
)
_INERT = (
    "//*[@hidden] | //*[@aria-hidden='true'] | //template | //noscript"
    " | //script | //style"
)


def markdown_available() -> bool:
    """Whether Scrapling and markdownify are installed (found, not imported)."""
    return all(
        importlib.util.find_spec(m) is not None for m in ("scrapling", "markdownify")
    )


def _visible(html: str) -> str:
    """``html`` without the elements a reader never sees (their tail text stays)."""
    import lxml.html

    try:
        root = lxml.html.document_fromstring(html)
    except (ValueError, lxml.etree.ParserError):
        return html
    for element in root.xpath(_INERT):
        element.drop_tree()
    return lxml.html.tostring(root, encoding="unicode")


def page_markdown(
    html: str,
    url: str = "",
    *,
    css_selector: str | None = None,
    main_content_only: bool = True,
) -> str:
    """``html`` as Markdown: the ``<body>`` (or the elements ``css_selector`` matches).

    Raises:
        ValueError: ``css_selector`` is not a valid CSS selector.
        ModuleNotFoundError: Scrapling or markdownify is not installed.
    """
    from cssselect import SelectorError
    from scrapling.engines.toolbelt.custom import Response

    # Scrapling logs every Response it builds at INFO on its own handler.
    logging.getLogger("scrapling").setLevel(logging.WARNING)
    page = Response(
        url=url,
        content=_visible(html),
        status=200,
        reason="OK",
        cookies={},
        headers={},
        request_headers={},
    )
    try:
        text = page.markdown(
            css_selector=css_selector, main_content_only=main_content_only
        )
    except SelectorError as exc:
        raise ValueError(f"invalid CSS selector: {css_selector}") from exc
    return str(text).strip()


class MarkdownExtractor(BaseExtractor):
    """The page's main content as Markdown (``None`` for an empty page or without Scrapling)."""

    def extract(self, page: ScrapedPage, base_url: str | None = None) -> Any:
        if not page.html.strip() or not markdown_available():
            return None
        return page_markdown(page.html, base_url or page.final_url) or None


__all__ = ["INSTALL_HINT", "MarkdownExtractor", "markdown_available", "page_markdown"]

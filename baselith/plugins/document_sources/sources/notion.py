"""Notion document source.

Indexes the pages a Notion integration has been shared with. Either every
page the integration can see (the default) or the pages under the configured
roots, children included. Pages become markdown documents; the markdown
splitter then keeps the heading structure as chunk metadata.

Egress is fixed to ``api.notion.com``: the host is not configurable, so the
integration token can only ever be sent there. The token is a ``SecretStr``,
used for one header and never logged, and error messages carry the status code
only, never the response body.
"""

from __future__ import annotations

import asyncio
from collections import deque
from collections.abc import AsyncIterator, Sequence
from typing import Any

import httpx
from pydantic import SecretStr

from baselith.core.config import get_processing_config
from baselith.core.observability.logging import get_logger
from baselith.core.security.http import create_hardened_async_client

from ..models import DocumentItem, DocumentSourceError
from ..utils import compute_remote_fingerprint
from .notion_blocks import block_to_lines, normalise_id, page_title
from .registry import register_source

logger = get_logger(__name__)

NOTION_API = "https://api.notion.com/v1"
NOTION_VERSION = "2022-06-28"
#: Nesting followed inside one page, and blocks read from it.
MAX_DEPTH = 6
MAX_BLOCKS_PER_PAGE = 2000
#: A page with less text than this is a stub, not a document.
MIN_CHARS = 20
MAX_CHARS = 1_000_000
#: Notion averages about three requests a second per integration.
DEFAULT_MIN_INTERVAL = 0.34
_MAX_RETRY_AFTER = 30.0
_RETRIES = 3


class NotionAuthError(DocumentSourceError):
    """Notion refused the integration token; no page can be read."""


class NotionDocumentSource:
    """Document source over the Notion REST API."""

    def __init__(
        self,
        token: SecretStr,
        root_page_ids: Sequence[str] = (),
        *,
        max_pages: int = 200,
        min_interval: float = DEFAULT_MIN_INTERVAL,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        """
        Args:
            token: The integration token.
            root_page_ids: Pages to index with their sub-pages; empty means every
                page shared with the integration.
            max_pages: Stop after this many pages.
            min_interval: Seconds between requests (Notion rate limit).
            client: An HTTP client (tests); a hardened one is built otherwise.

        Raises:
            DocumentSourceError: A root id is not a Notion id.
        """
        roots = []
        for raw in root_page_ids:
            page_id = normalise_id(raw)
            if page_id is None:
                raise DocumentSourceError(
                    "NOTION_ROOT_PAGE_IDS holds an invalid page id"
                )
            roots.append(page_id)
        self._token = token
        self._roots = roots
        self._max_pages = max_pages
        self._min_interval = min_interval
        self._client = client
        self._owns_client = client is None
        self._last_request = 0.0

    # ------------------------------------------------------------------ HTTP

    def _http(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = create_hardened_async_client(timeout=30.0)
        return self._client

    async def _request(self, method: str, path: str, **kwargs: Any) -> dict[str, Any]:
        """One API call with throttling and bounded retries."""
        headers = {
            "Authorization": f"Bearer {self._token.get_secret_value()}",
            "Notion-Version": NOTION_VERSION,
        }
        for attempt in range(1, _RETRIES + 1):
            wait = self._min_interval - (
                asyncio.get_running_loop().time() - self._last_request
            )
            if wait > 0:
                await asyncio.sleep(wait)
            try:
                response = await self._http().request(
                    method, f"{NOTION_API}{path}", headers=headers, **kwargs
                )
            except httpx.HTTPError as exc:
                raise DocumentSourceError(
                    f"Notion request failed: {type(exc).__name__}"
                ) from exc
            finally:
                self._last_request = asyncio.get_running_loop().time()
            status = response.status_code
            if status == 429 and attempt < _RETRIES:
                await asyncio.sleep(_retry_after(response))
                continue
            if status >= 500 and attempt < _RETRIES:
                await asyncio.sleep(float(attempt))
                continue
            if status == 401:
                raise NotionAuthError("Notion rejected the integration token")
            if status >= 400:
                raise DocumentSourceError(
                    f"Notion answered {status} for {method} {path.split('?')[0]}"
                )
            data = response.json()
            if not isinstance(data, dict):
                raise DocumentSourceError("Notion returned an unexpected payload")
            return data
        raise DocumentSourceError(
            "Notion kept answering with errors"
        )  # pragma: no cover

    async def _paged(
        self, method: str, path: str, **kwargs: Any
    ) -> AsyncIterator[dict[str, Any]]:
        """Every result of a paginated endpoint."""
        cursor: str | None = None
        while True:
            params = dict(kwargs.get("params") or {}, page_size=100)
            body = dict(kwargs.get("json") or {})
            if method == "POST":
                body["page_size"] = 100
                if cursor:
                    body["start_cursor"] = cursor
                data = await self._request(method, path, json=body)
            else:
                if cursor:
                    params["start_cursor"] = cursor
                data = await self._request(method, path, params=params)
            for result in data.get("results") or []:
                if isinstance(result, dict):
                    yield result
            cursor = data.get("next_cursor") if data.get("has_more") else None
            if not cursor:
                return

    # ------------------------------------------------------------- traversal

    async def _page_markdown(self, page_id: str, children: deque[str]) -> str:
        """The page body as markdown; sub-pages found are queued in ``children``."""
        lines: list[str] = []
        budget = [MAX_BLOCKS_PER_PAGE]

        async def walk(block_id: str, depth: int) -> None:
            async for block in self._paged("GET", f"/blocks/{block_id}/children"):
                if budget[0] <= 0:
                    lines.append("[page truncated]")
                    return
                budget[0] -= 1
                if block.get("type") == "child_page":
                    child = normalise_id(block.get("id"))
                    if child:
                        children.append(child)
                    continue
                lines.extend(block_to_lines(block, depth))
                nested = normalise_id(block.get("id"))
                if block.get("has_children") and nested and depth < MAX_DEPTH:
                    await walk(nested, depth + 1)

        await walk(page_id, 0)
        return "\n".join(lines)

    async def _item(
        self, page: dict[str, Any], children: deque[str]
    ) -> DocumentItem | None:
        page_id = normalise_id(page.get("id"))
        if page_id is None or page.get("archived") or page.get("in_trash"):
            return None
        title = page_title(page) or "Untitled"
        body = await self._page_markdown(page_id, children)
        content = f"# {title}\n\n{body}".strip()[:MAX_CHARS]
        if len(body.strip()) < MIN_CHARS:
            return None
        uid = f"notion:{page_id}"
        return DocumentItem(
            uid=uid,
            content=content,
            fingerprint=compute_remote_fingerprint(uid, content),
            metadata={
                "origin": "notion",
                "doc_type": "notion",
                "source": str(page.get("url") or uid),
                "title": title,
                "page_id": page_id,
                "last_edited": str(page.get("last_edited_time") or ""),
            },
        )

    async def _roots_pages(self, children: deque[str]) -> AsyncIterator[dict[str, Any]]:
        """The configured pages, then their sub-pages, breadth first."""
        queue: deque[str] = deque(self._roots)
        seen: set[str] = set()
        while queue:
            page_id = queue.popleft()
            if page_id in seen:
                continue
            seen.add(page_id)
            try:
                yield await self._request("GET", f"/pages/{page_id}")
            except NotionAuthError:
                raise
            except DocumentSourceError as exc:
                logger.warning("[notion-source] skipping a page: %s", exc)
            queue.extend(children)
            children.clear()

    async def _searched_pages(self) -> AsyncIterator[dict[str, Any]]:
        """Every page the integration can see."""
        async for page in self._paged(
            "POST", "/search", json={"filter": {"value": "page", "property": "object"}}
        ):
            yield page

    async def iter_items(self) -> AsyncIterator[DocumentItem]:
        """Yield one document per readable page, up to ``max_pages``."""
        emitted = 0
        children: deque[str] = deque()
        pages = self._roots_pages(children) if self._roots else self._searched_pages()
        async for page in pages:
            if emitted >= self._max_pages:
                return
            item = await self._item(page, children)
            if not self._roots:
                children.clear()  # a search already lists every shared page
            if item is not None:
                emitted += 1
                yield item

    async def close(self) -> None:
        """Close the HTTP client when this source built it."""
        if self._client is not None and self._owns_client:
            await self._client.aclose()


def _retry_after(response: httpx.Response) -> float:
    try:
        return min(
            max(float(response.headers.get("retry-after", "1")), 0.0), _MAX_RETRY_AFTER
        )
    except ValueError:
        return 1.0


def _create_notion_source() -> NotionDocumentSource | None:
    """Factory: inert until ``NOTION_API_TOKEN`` is set."""
    config = get_processing_config()
    if (
        config.notion_api_token is None
        or not config.notion_api_token.get_secret_value()
    ):
        return None
    try:
        return NotionDocumentSource(
            config.notion_api_token,
            config.notion_root_page_ids,
            max_pages=config.notion_max_pages,
        )
    except DocumentSourceError as exc:
        logger.warning("[notion-source] %s", exc)
        return None


register_source("notion", _create_notion_source)

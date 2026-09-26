"""Playwright request guard for the web document source.

The crawler screens each URL before ``page.goto`` and the final URL after it,
but a rendered page issues many more requests than that: server-side redirect
hops, scripts, images, ``fetch``/XHR, iframes. Each of them is made by the
browser from inside the network the indexer runs in, so without a route
handler a crawled page could reach the metadata endpoint or an internal
service through a sub-resource — the post-navigation check only discards the
*content*, after the request has already happened.
"""

from __future__ import annotations

from typing import Any

from core.observability.logging import get_logger
from core.security.ssrf import assert_url_safe_async

logger = get_logger(__name__)


async def ssrf_route_guard(route: Any, request: Any) -> None:
    """Abort any browser request whose target fails the SSRF screen.

    Registered with ``context.route("**/*", ssrf_route_guard)``. DNS runs off
    the event loop (:func:`assert_url_safe_async`). Fails closed: any error,
    expected or not, aborts the request.
    """
    try:
        await assert_url_safe_async(request.url)
    except Exception:
        logger.warning(f"[web-source] SSRF-blocked browser request {request.url}")
        await route.abort("blockedbyclient")
        return
    await route.continue_()


__all__ = ["ssrf_route_guard"]

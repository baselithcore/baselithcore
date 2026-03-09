# core/scraper/middleware/base.py
"""Base middleware class and chain."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING, Callable, Awaitable

if TYPE_CHECKING:
    from ..models import ScrapedPage


class BaseMiddleware(ABC):
    """Abstract base class for middleware components."""

    @abstractmethod
    async def process_request(self, url: str) -> str | None:
        """Process a request before fetching.

        Args:
            url: The URL being requested.

        Returns:
            Modified URL, or None to skip the request.
        """
        ...

    @abstractmethod
    async def process_response(self, url: str, page: ScrapedPage) -> ScrapedPage:
        """Process a response after fetching.

        Args:
            url: The original URL.
            page: The fetched page.

        Returns:
            Potentially modified page.
        """
        ...


class MiddlewareChain:
    """Chain of middleware components.

    Middleware are executed in order for requests and reverse order for responses.
    """

    def __init__(self, middlewares: list[BaseMiddleware] | None = None):
        """Initialize the middleware chain.

        Args:
            middlewares: List of middleware instances.
        """
        self.middlewares = middlewares or []

    def add(self, middleware: BaseMiddleware) -> MiddlewareChain:
        """Add a middleware to the chain.

        Args:
            middleware: Middleware instance to add.

        Returns:
            Self for chaining.
        """
        self.middlewares.append(middleware)
        return self

    async def process_request(self, url: str) -> str | None:
        """Process request through all middleware.

        Args:
            url: The URL being requested.

        Returns:
            Modified URL or None to skip.
        """
        current_url: str | None = url

        for middleware in self.middlewares:
            if current_url is None:
                break
            current_url = await middleware.process_request(current_url)

        return current_url

    async def process_response(self, url: str, page: ScrapedPage) -> ScrapedPage:
        """Process response through all middleware (reverse order).

        Args:
            url: The original URL.
            page: The fetched page.

        Returns:
            Potentially modified page.
        """
        current_page = page

        # Process in reverse order
        for middleware in reversed(self.middlewares):
            current_page = await middleware.process_response(url, current_page)

        return current_page

    async def wrap_fetch(
        self,
        url: str,
        fetch_fn: Callable[[str], Awaitable[ScrapedPage]],
    ) -> ScrapedPage | None:
        """Wrap a fetch function with middleware processing.

        Args:
            url: The URL to fetch.
            fetch_fn: The actual fetch function.

        Returns:
            Processed page or None if skipped.
        """
        # Process request
        processed_url = await self.process_request(url)
        if processed_url is None:
            return None

        # Execute fetch
        page = await fetch_fn(processed_url)

        # Process response
        return await self.process_response(url, page)

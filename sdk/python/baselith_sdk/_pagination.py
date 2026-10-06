"""Cursor pagination and run-polling helpers shared by both clients.

Every list endpoint answers ``{<items>, "count", "next_cursor", "has_more"}``
and takes ``limit`` / ``cursor`` query parameters; :func:`iter_pages` follows
``next_cursor`` until the last page. :func:`wait_for_run` polls an async run's
status until it is terminal.
"""

from __future__ import annotations

import asyncio
import time
from typing import Any, AsyncIterator, Awaitable, Callable, Iterator, TypeVar

from .errors import BaselithConfigError, RunTimeoutError
from .models import AgentRunStatus, Page

P = TypeVar("P", bound=Page)


def iter_pages(fetch: Callable[..., P], /, *args: Any, **kwargs: Any) -> Iterator[P]:
    """Yield every page of a cursor-paginated listing, following ``next_cursor``.

    Args:
        fetch: A list method of :class:`~baselith_sdk.BaselithClient` (e.g.
            ``client.list_webhooks``) — anything taking ``cursor=`` and
            returning a :class:`~baselith_sdk.models.Page`.
        *args: Positional arguments for every call (``run_id`` for
            ``get_run_history``).
        **kwargs: Passed to every call (``limit=``, filters). A ``cursor=``
            here starts from that cursor instead of the first page.
    """
    cursor = kwargs.pop("cursor", None)
    while True:
        page = fetch(*args, cursor=cursor, **kwargs)
        yield page
        if not page.has_more or not page.next_cursor or page.next_cursor == cursor:
            return
        cursor = page.next_cursor


async def aiter_pages(
    fetch: Callable[..., Awaitable[P]], /, *args: Any, **kwargs: Any
) -> AsyncIterator[P]:
    """Async counterpart of :func:`iter_pages` for ``AsyncBaselithClient``."""
    cursor = kwargs.pop("cursor", None)
    while True:
        page = await fetch(*args, cursor=cursor, **kwargs)
        yield page
        if not page.has_more or not page.next_cursor or page.next_cursor == cursor:
            return
        cursor = page.next_cursor


def _check_wait_args(timeout: float, poll_interval: float) -> None:
    if timeout < 0:
        raise BaselithConfigError("timeout must be >= 0")
    if poll_interval <= 0:
        raise BaselithConfigError("poll_interval must be > 0")


def wait_for_run(
    get_status: Callable[[str], AgentRunStatus],
    task_id: str,
    timeout: float,
    poll_interval: float,
) -> AgentRunStatus:
    """Poll ``get_status(task_id)`` until terminal; :class:`RunTimeoutError` after ``timeout``."""
    _check_wait_args(timeout, poll_interval)
    deadline = time.monotonic() + timeout
    while True:
        status = get_status(task_id)
        if status.is_terminal:
            return status
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise RunTimeoutError(task_id, timeout, status)
        time.sleep(min(poll_interval, remaining))


async def await_run(
    get_status: Callable[[str], Awaitable[AgentRunStatus]],
    task_id: str,
    timeout: float,
    poll_interval: float,
) -> AgentRunStatus:
    """Async counterpart of :func:`wait_for_run`."""
    _check_wait_args(timeout, poll_interval)
    deadline = time.monotonic() + timeout
    while True:
        status = await get_status(task_id)
        if status.is_terminal:
            return status
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise RunTimeoutError(task_id, timeout, status)
        await asyncio.sleep(min(poll_interval, remaining))

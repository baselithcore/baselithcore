"""
Optional landing redirect for ``/``.

The framework registers no route at the site root: what an operator considers
the homepage is one of the plugin SPAs the deployment installed
(``/<plugin>/``), and core cannot guess which. A browser sent to the bare
hostname therefore lands on a 404 that looks like an outage — the first thing a
visitor sees.

``BASELITH_ROOT_REDIRECT`` names the landing; when it is set, ``GET /`` and
``HEAD /`` answer ``307`` pointing at it. The
redirect is **temporary** on purpose: ``301``/``308`` are cached by browsers
indefinitely and survive the setting being changed or removed, stranding every
visitor who ever hit the old root on a path that may no longer exist.

Validation of the target lives in :class:`core.config.app.AppConfig` so a bad
value fails the boot instead of turning an unauthenticated route into an open
redirect.
"""

from __future__ import annotations

from fastapi import APIRouter
from fastapi.responses import RedirectResponse
from starlette.status import HTTP_307_TEMPORARY_REDIRECT

__all__ = ["create_root_redirect_router"]


def create_root_redirect_router(target: str) -> APIRouter:
    """Build a router that redirects ``/`` to *target*.

    Args:
        target: Site-relative path to land on, e.g. ``/<plugin>/``. Already
            validated by the configuration layer.

    Returns:
        APIRouter: Router exposing the root redirect. Unauthenticated — it
        discloses only the landing path an anonymous visitor is about to be
        sent to — and kept out of the OpenAPI schema, which describes the API
        rather than the console's entry point.
    """
    router = APIRouter()

    # HEAD is listed explicitly: FastAPI registers exactly the methods it is
    # given, so a plain ``@router.get`` leaves ``HEAD /`` answering 405 — and
    # HEAD on the root is what uptime probes and link previewers send.
    @router.api_route("/", methods=["GET", "HEAD"], include_in_schema=False)
    async def root_redirect() -> RedirectResponse:
        """Send the bare hostname to the configured landing."""
        return RedirectResponse(url=target, status_code=HTTP_307_TEMPORARY_REDIRECT)

    return router

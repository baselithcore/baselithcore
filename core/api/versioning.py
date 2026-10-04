"""API versioning: the ``/v1`` mount and the deprecation of unprefixed paths.

Every data/API router is served twice: under ``/v1`` (the stable, versioned
contract new clients pin to — the SDKs already do) and at its historical
unprefixed path, kept live so existing clients do not break. The unprefixed
copy is included with ``deprecated=True`` — which marks its operations
deprecated in the OpenAPI document — and with a tiny marker dependency that
flags the request scope, so
:class:`core.middleware.api_deprecation.APIDeprecationMiddleware` can announce
it on the wire with an RFC 9745 ``Deprecation`` header and a
``Link: </v1/...>; rel="successor-version"`` (RFC 5829). A dependency rather
than the matched route's ``deprecated`` attribute, because FastAPI keeps the
include-level flag on a private per-include context, not on the route.

Operational surfaces are *not* versioned or deprecated: health/readiness
probes, ``/metrics``, ``/status``, the ``/admin`` dashboard and the discovery
documents keep their single unprefixed path, because probes, Prometheus,
nginx and the Helm chart address them directly.

``API_V1_ENABLED=false`` turns the ``/v1`` copies off; the unprefixed paths
are then the only ones and are not marked deprecated.
"""

from __future__ import annotations

import os
from collections.abc import Iterable
from typing import Any, Final

from fastapi import APIRouter, Depends, Request

#: Path prefix of the versioned API.
V1_PREFIX: Final = "/v1"

#: When the unprefixed API paths were deprecated (2026-10-04T00:00:00Z), as
#: the RFC 9745 structured-field date the ``Deprecation`` header carries.
DEPRECATION_TIMESTAMP: Final = 1791072000
DEPRECATION_HEADER_VALUE: Final = f"@{DEPRECATION_TIMESTAMP}"

#: ASGI scope key the marker dependency sets on a deprecated-path request.
DEPRECATED_SCOPE_KEY: Final = "baselith.deprecated_api_path"


async def mark_deprecated_path(request: Request) -> None:
    """Dependency: flag this request as served by a deprecated unprefixed path.

    Args:
        request: The incoming request; its ASGI scope gets
            :data:`DEPRECATED_SCOPE_KEY` set, which
            :class:`~core.middleware.api_deprecation.APIDeprecationMiddleware`
            reads when the response starts.
    """
    request.scope[DEPRECATED_SCOPE_KEY] = True


def api_v1_enabled() -> bool:
    """Whether the ``/v1`` copies are mounted (``API_V1_ENABLED``, default on)."""
    return os.getenv("API_V1_ENABLED", "true").strip().lower() in ("1", "true", "yes")


def legacy_include_kwargs() -> dict[str, Any]:
    """``include_router`` keywords for an unprefixed copy of an API router.

    Empty when ``/v1`` is disabled: the unprefixed path is then the only one
    and is not deprecated.
    """
    if not api_v1_enabled():
        return {}
    return {"deprecated": True, "dependencies": [Depends(mark_deprecated_path)]}


def versioned_routers(routers: Iterable[APIRouter]) -> list[APIRouter]:
    """Wrap API routers as a deprecated unprefixed copy plus a ``/v1`` copy.

    Returns the routers unchanged when ``/v1`` is disabled. Each wrapper is
    its own ``APIRouter`` so the original router objects are never mutated
    (the app factory and the plugin may both hold them).

    Args:
        routers: The API routers to expose.

    Returns:
        Every unprefixed (deprecated) copy first, then every ``/v1`` copy, so
        route matching order between the routers is preserved in each group.
    """
    originals = list(routers)
    if not api_v1_enabled():
        return originals
    legacy: list[APIRouter] = []
    current: list[APIRouter] = []
    for router in originals:
        unprefixed = APIRouter()
        unprefixed.include_router(router, **legacy_include_kwargs())
        legacy.append(unprefixed)
        v1 = APIRouter(prefix=V1_PREFIX)
        v1.include_router(router)
        current.append(v1)
    return legacy + current


__all__ = [
    "DEPRECATED_SCOPE_KEY",
    "DEPRECATION_HEADER_VALUE",
    "DEPRECATION_TIMESTAMP",
    "V1_PREFIX",
    "api_v1_enabled",
    "legacy_include_kwargs",
    "mark_deprecated_path",
    "versioned_routers",
]

"""Standardized JSON error envelope and exception handlers.

Enterprise API consumers expect a single, predictable error shape with a
correlation id. This module provides that envelope and registers handlers for:

- :class:`core.exceptions.BaselithError` — mapped to an appropriate HTTP status.
- bare :class:`Exception` — the catch-all that previously surfaced as Starlette's
  generic ``Internal Server Error`` with no body.

It deliberately does **not** override FastAPI's ``HTTPException`` or
``RequestValidationError`` handlers, so existing endpoints that raise those keep
their current ``{"detail": ...}`` responses unchanged — this is purely additive.

Envelope shape::

    {
      "error": {
        "code": "not_found",          # stable, machine-readable
        "message": "…",               # human-readable
        "type": "ItemNotFoundError",  # exception class name
        "request_id": "…"             # correlation id (X-Request-ID)
      }
    }
"""

from __future__ import annotations

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from core.auth.types import (
    InsufficientPermissionsError,
    InsufficientScopeError,
)
from core.exceptions import (
    BaselithError,
    DuplicateRegistrationError,
    ItemNotFoundError,
    PluginConfigError,
    PluginDependencyError,
    PluginIntegrityError,
)
from core.observability.logging import get_logger
from core.observability.setup import request_id_ctx

logger = get_logger(__name__)

#: Maps BaselithError subclasses to (HTTP status, stable error code).
#: Order matters: most specific first (checked via isinstance).
_ERROR_MAP: list[tuple[type[BaselithError], int, str]] = [
    (ItemNotFoundError, 404, "not_found"),
    (DuplicateRegistrationError, 409, "conflict"),
    (PluginConfigError, 400, "invalid_configuration"),
    (PluginIntegrityError, 403, "integrity_error"),
    (PluginDependencyError, 409, "dependency_error"),
]
_DEFAULT_STATUS = 500
_DEFAULT_CODE = "internal_error"


def _current_request_id() -> str | None:
    """Best-effort fetch of the active request id (set by RequestIdMiddleware)."""
    try:
        return request_id_ctx.get(None)
    except Exception:  # noqa: BLE001 — never fail while building an error
        return None


def error_envelope(
    *,
    status_code: int,
    code: str,
    message: str,
    error_type: str,
    request_id: str | None = None,
) -> JSONResponse:
    """Build a standardized error :class:`JSONResponse`."""
    body = {
        "error": {
            "code": code,
            "message": message,
            "type": error_type,
            "request_id": request_id
            if request_id is not None
            else _current_request_id(),
        }
    }
    return JSONResponse(status_code=status_code, content=body)


def _map_baselith_error(exc: BaselithError) -> tuple[int, str]:
    """Resolve (status, code) for a BaselithError via the mapping table."""
    for exc_type, status_code, code in _ERROR_MAP:
        if isinstance(exc, exc_type):
            return status_code, code
    return _DEFAULT_STATUS, _DEFAULT_CODE


async def baselith_exception_handler(
    request: Request, exc: BaselithError
) -> JSONResponse:
    """Render a :class:`BaselithError` as a mapped, enveloped response."""
    status_code, code = _map_baselith_error(exc)
    message = str(exc) or exc.__class__.__name__
    if status_code >= 500:
        logger.error("Unhandled BaselithError: %s", exc, exc_info=exc)
    return error_envelope(
        status_code=status_code,
        code=code,
        message=message,
        error_type=exc.__class__.__name__,
    )


async def insufficient_permissions_handler(
    request: Request, exc: InsufficientPermissionsError
) -> JSONResponse:
    """Render an authorization failure as a 403 envelope.

    Covers both missing-role (:class:`InsufficientPermissionsError`) and
    missing-capability (:class:`InsufficientScopeError`) denials raised by the
    ``require_auth`` / ``require_scopes`` / ``enforce_scopes`` choke points.
    """
    code = (
        "insufficient_scope"
        if isinstance(exc, InsufficientScopeError)
        else "insufficient_permissions"
    )
    return error_envelope(
        status_code=403,
        code=code,
        message=str(exc) or "Insufficient permissions.",
        error_type=exc.__class__.__name__,
    )


async def unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    """Catch-all: log the exception and return a generic 500 envelope.

    The message is intentionally generic to avoid leaking internals; the
    ``request_id`` lets operators correlate it with the logged traceback.
    """
    logger.error(
        "Unhandled exception on %s %s", request.method, request.url.path, exc_info=exc
    )
    return error_envelope(
        status_code=_DEFAULT_STATUS,
        code=_DEFAULT_CODE,
        message="Internal server error.",
        error_type=exc.__class__.__name__,
    )


def install_error_handlers(app: FastAPI) -> None:
    """Register the envelope handlers on the FastAPI app.

    Does not touch ``HTTPException`` / ``RequestValidationError`` handlers, so
    existing endpoint responses are unaffected.
    """
    app.add_exception_handler(BaselithError, baselith_exception_handler)  # type: ignore[arg-type]
    app.add_exception_handler(
        InsufficientPermissionsError,
        insufficient_permissions_handler,  # type: ignore[arg-type]
    )
    app.add_exception_handler(Exception, unhandled_exception_handler)
    logger.debug("Standardized error envelope handlers installed.")


__all__ = [
    "error_envelope",
    "install_error_handlers",
    "baselith_exception_handler",
    "insufficient_permissions_handler",
    "unhandled_exception_handler",
]

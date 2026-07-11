"""RFC 9457 ``application/problem+json`` error responses and handlers.

Enterprise API consumers expect a single, predictable, standards-based error
shape with a correlation id. This module renders **every** error as an
`RFC 9457 <https://www.rfc-editor.org/rfc/rfc9457>`_ problem document and
registers handlers for the full surface so the API never emits two shapes:

- :class:`core.exceptions.BaselithError` — mapped to an appropriate HTTP status.
- :class:`~starlette.exceptions.HTTPException` — every ``raise HTTPException(...)``
  (auth, not-found, method-not-allowed, …). ``detail`` is preserved as the
  RFC 9457 ``detail`` field, so existing consumers reading ``response["detail"]``
  keep working; any ``headers`` (e.g. ``WWW-Authenticate``, ``Retry-After``) are
  carried through.
- :class:`~fastapi.exceptions.RequestValidationError` — the per-field errors are
  attached under the ``errors`` extension member.
- bare :class:`Exception` — the catch-all that previously surfaced as Starlette's
  generic ``Internal Server Error`` with no body.

Document shape (``Content-Type: application/problem+json``)::

    {
      "type": "urn:baselith:error:not_found",  # stable machine classifier
      "title": "Not Found",                     # short human summary
      "status": 404,
      "detail": "…",                            # human-readable explanation
      "instance": "/v1/items/42",               # the request path
      "code": "not_found",                       # extension: stable code (back-compat)
      "request_id": "…",                         # extension: correlation id
      "errors": [ … ]                            # extension: validation only
    }
"""

from __future__ import annotations

import http
from typing import Any

from fastapi import FastAPI, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

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
from core.middleware.cost_control import BudgetExceededError
from core.observability.logging import get_logger
from core.observability.setup import request_id_ctx
from core.quotas.manager import QuotaExceededError

logger = get_logger(__name__)

#: Media type mandated by RFC 9457 for problem documents.
PROBLEM_JSON_MEDIA_TYPE = "application/problem+json"

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

#: Stable machine codes for common HTTPException statuses.
_HTTP_CODE_BY_STATUS: dict[int, str] = {
    400: "bad_request",
    401: "unauthorized",
    403: "forbidden",
    404: "not_found",
    405: "method_not_allowed",
    406: "not_acceptable",
    409: "conflict",
    410: "gone",
    413: "payload_too_large",
    415: "unsupported_media_type",
    422: "unprocessable_entity",
    429: "rate_limited",
    500: "internal_error",
    502: "bad_gateway",
    503: "service_unavailable",
    504: "gateway_timeout",
}


def _current_request_id() -> str | None:
    """Best-effort fetch of the active request id (set by RequestIdMiddleware)."""
    try:
        return request_id_ctx.get(None)
    except Exception:
        return None


def _title_for(status_code: int) -> str:
    """Human-readable title from the HTTP status phrase (RFC 9457 default)."""
    try:
        return http.HTTPStatus(status_code).phrase
    except ValueError:
        return "Error"


def problem_response(
    *,
    status_code: int,
    code: str,
    detail: str,
    error_type: str | None = None,
    title: str | None = None,
    instance: str | None = None,
    request_id: str | None = None,
    extra: dict[str, Any] | None = None,
    headers: dict[str, str] | None = None,
) -> JSONResponse:
    """Build an RFC 9457 ``application/problem+json`` response."""
    body: dict[str, Any] = {
        "type": f"urn:baselith:error:{code}",
        "title": title or _title_for(status_code),
        "status": status_code,
        "detail": detail,
        "code": code,
        "request_id": request_id if request_id is not None else _current_request_id(),
    }
    if instance:
        body["instance"] = instance
    if error_type:
        body["error_type"] = error_type
    if extra:
        body.update(extra)
    return JSONResponse(
        status_code=status_code,
        content=body,
        media_type=PROBLEM_JSON_MEDIA_TYPE,
        headers=headers,
    )


def error_envelope(
    *,
    status_code: int,
    code: str,
    message: str,
    error_type: str,
    request_id: str | None = None,
) -> JSONResponse:
    """Back-compat shim: build a problem document from the legacy signature."""
    return problem_response(
        status_code=status_code,
        code=code,
        detail=message,
        error_type=error_type,
        request_id=request_id,
    )


def _map_baselith_error(exc: BaselithError) -> tuple[int, str]:
    """Resolve (status, code) for a BaselithError via the mapping table."""
    for exc_type, status_code, code in _ERROR_MAP:
        if isinstance(exc, exc_type):
            return status_code, code
    return _DEFAULT_STATUS, _DEFAULT_CODE


async def baselith_exception_handler(
    request: Request, exc: BaselithError
) -> JSONResponse:
    """Render a :class:`BaselithError` as a mapped problem document."""
    status_code, code = _map_baselith_error(exc)
    message = str(exc) or exc.__class__.__name__
    if status_code >= 500:
        logger.error("Unhandled BaselithError: %s", exc, exc_info=exc)
    return problem_response(
        status_code=status_code,
        code=code,
        detail=message,
        error_type=exc.__class__.__name__,
        instance=request.url.path,
    )


async def insufficient_permissions_handler(
    request: Request, exc: InsufficientPermissionsError
) -> JSONResponse:
    """Render an authorization failure as a 403 problem document.

    Covers both missing-role (:class:`InsufficientPermissionsError`) and
    missing-capability (:class:`InsufficientScopeError`) denials raised by the
    ``require_auth`` / ``require_scopes`` / ``enforce_scopes`` choke points.
    """
    code = (
        "insufficient_scope"
        if isinstance(exc, InsufficientScopeError)
        else "insufficient_permissions"
    )
    return problem_response(
        status_code=403,
        code=code,
        detail=str(exc) or "Insufficient permissions.",
        error_type=exc.__class__.__name__,
        instance=request.url.path,
    )


async def quota_exceeded_handler(
    request: Request, exc: QuotaExceededError
) -> JSONResponse:
    """Render a quota breach as a 429 problem document."""
    return problem_response(
        status_code=429,
        code="quota_exceeded",
        detail=str(exc) or "Usage quota exceeded.",
        error_type=exc.__class__.__name__,
        instance=request.url.path,
    )


async def budget_exceeded_handler(
    request: Request, exc: BudgetExceededError
) -> JSONResponse:
    """Render a per-request cost-budget breach as a 429 problem document.

    ``CostControlMiddleware`` catches :class:`BudgetExceededError` only at the
    ASGI boundary; one raised deep inside application code is intercepted first
    by Starlette's ``ExceptionMiddleware`` (the innermost layer), which without
    this handler renders it as a generic 500. Registering the specific handler
    keeps every budget breach (tokens, graph queries, SQL queries) a 429.
    """
    return problem_response(
        status_code=429,
        code="budget_exceeded",
        detail=str(exc) or "Request cost budget exceeded.",
        error_type=exc.__class__.__name__,
        instance=request.url.path,
    )


async def http_exception_handler(
    request: Request, exc: StarletteHTTPException
) -> JSONResponse:
    """Render any ``HTTPException`` as a problem document.

    ``detail`` (a str for the vast majority of raises) becomes the RFC 9457
    ``detail`` field, so consumers already reading ``response["detail"]`` are
    unaffected. Response ``headers`` are preserved (e.g. ``WWW-Authenticate`` on
    401, ``Retry-After`` on 429).
    """
    detail = exc.detail if isinstance(exc.detail, str) else str(exc.detail)
    code = _HTTP_CODE_BY_STATUS.get(exc.status_code, "http_error")
    headers = getattr(exc, "headers", None)
    return problem_response(
        status_code=exc.status_code,
        code=code,
        detail=detail,
        error_type="HTTPException",
        instance=request.url.path,
        headers=headers,
    )


async def validation_exception_handler(
    request: Request, exc: RequestValidationError
) -> JSONResponse:
    """Render request validation failures as a problem document.

    Per-field errors are attached under the ``errors`` extension member (run
    through ``jsonable_encoder`` since raw entries may hold non-serializable
    context such as the originating exception).
    """
    return problem_response(
        status_code=422,
        code="validation_error",
        detail="Request validation failed.",
        error_type="RequestValidationError",
        instance=request.url.path,
        extra={"errors": jsonable_encoder(exc.errors())},
    )


async def unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    """Catch-all: log the exception and return a generic 500 problem document.

    The detail is intentionally generic to avoid leaking internals; the
    ``request_id`` lets operators correlate it with the logged traceback.
    """
    logger.error(
        "Unhandled exception on %s %s", request.method, request.url.path, exc_info=exc
    )
    return problem_response(
        status_code=_DEFAULT_STATUS,
        code=_DEFAULT_CODE,
        detail="Internal server error.",
        error_type=exc.__class__.__name__,
        instance=request.url.path,
    )


def install_error_handlers(app: FastAPI) -> None:
    """Register the RFC 9457 problem+json handlers on the FastAPI app.

    Overrides the default ``HTTPException`` and ``RequestValidationError``
    handlers so the entire API emits a single, uniform error shape.
    """
    app.add_exception_handler(BaselithError, baselith_exception_handler)  # type: ignore[arg-type]
    app.add_exception_handler(
        InsufficientPermissionsError,
        insufficient_permissions_handler,  # type: ignore[arg-type]
    )
    app.add_exception_handler(
        QuotaExceededError,
        quota_exceeded_handler,  # type: ignore[arg-type]
    )
    app.add_exception_handler(
        BudgetExceededError,
        budget_exceeded_handler,  # type: ignore[arg-type]
    )
    app.add_exception_handler(
        StarletteHTTPException,
        http_exception_handler,  # type: ignore[arg-type]
    )
    app.add_exception_handler(
        RequestValidationError,
        validation_exception_handler,  # type: ignore[arg-type]
    )
    app.add_exception_handler(Exception, unhandled_exception_handler)
    logger.debug("RFC 9457 problem+json error handlers installed.")


__all__ = [
    "PROBLEM_JSON_MEDIA_TYPE",
    "baselith_exception_handler",
    "budget_exceeded_handler",
    "error_envelope",
    "http_exception_handler",
    "install_error_handlers",
    "insufficient_permissions_handler",
    "problem_response",
    "quota_exceeded_handler",
    "unhandled_exception_handler",
    "validation_exception_handler",
]

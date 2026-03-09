"""
Security headers middleware.

Adds standard security headers to all HTTP responses.
"""

from typing import Callable

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    """
    Middleware that adds security headers to all responses.

    Headers added:
    - X-Content-Type-Options: nosniff
    - X-Frame-Options: DENY
    - X-XSS-Protection: 1; mode=block
    - Strict-Transport-Security: max-age=31536000; includeSubDomains
    - Content-Security-Policy: configurable
    - Referrer-Policy: strict-origin-when-cross-origin
    - Permissions-Policy: configurable
    """

    def __init__(
        self,
        app: Callable,
        hsts_enabled: bool | None = None,
        hsts_max_age: int | None = None,
        frame_options: str | None = None,
        content_security_policy: str | None = None,
        permissions_policy: str | None = None,
    ):
        """
        Initialize security headers middleware.

        Args:
            app: ASGI application
            hsts_enabled: Enable Strict-Transport-Security header
            hsts_max_age: HSTS max-age in seconds (default: 1 year)
            frame_options: X-Frame-Options value (DENY, SAMEORIGIN)
            content_security_policy: Custom CSP header value
            permissions_policy: Custom Permissions-Policy header value
        """
        from core.config.security import get_security_config

        config = get_security_config()

        super().__init__(app)
        self.hsts_enabled = (
            hsts_enabled if hsts_enabled is not None else config.enable_hsts
        )
        self.hsts_max_age = (
            hsts_max_age if hsts_max_age is not None else config.hsts_max_age
        )
        self.frame_options = frame_options if frame_options else config.frame_options
        self.content_security_policy = (
            content_security_policy
            if content_security_policy
            else (config.content_security_policy or self._default_csp())
        )
        self.permissions_policy = (
            permissions_policy
            if permissions_policy
            else (config.permissions_policy or self._default_permissions())
        )

    def _default_csp(self) -> str:
        """Return default Content-Security-Policy."""
        return (
            "default-src 'self'; "
            "script-src 'self' 'unsafe-inline' 'unsafe-eval'; "
            "style-src 'self' 'unsafe-inline'; "
            "img-src 'self' data: https:; "
            "font-src 'self' data:; "
            "connect-src 'self' ws: wss:; "
            "frame-ancestors 'none';"
        )

    def _default_permissions(self) -> str:
        """Return default Permissions-Policy."""
        return (
            "accelerometer=(), "
            "camera=(), "
            "geolocation=(), "
            "gyroscope=(), "
            "magnetometer=(), "
            "microphone=(), "
            "payment=(), "
            "usb=()"
        )

    async def __call__(self, scope, receive, send) -> None:
        """
        Handle ASGI application call.

        Overrides base method to ensure only HTTP requests are processed.
        """
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        await super().__call__(scope, receive, send)

    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        """Add security headers to the response."""
        response = await call_next(request)

        # Prevent MIME type sniffing
        response.headers["X-Content-Type-Options"] = "nosniff"

        # Prevent clickjacking
        response.headers["X-Frame-Options"] = self.frame_options

        # Enable XSS filter (legacy browsers)
        response.headers["X-XSS-Protection"] = "1; mode=block"

        # Referrer policy
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"

        # Content Security Policy
        response.headers["Content-Security-Policy"] = self.content_security_policy

        # Permissions Policy
        response.headers["Permissions-Policy"] = self.permissions_policy

        # HSTS (only in production/HTTPS)
        if self.hsts_enabled:
            response.headers["Strict-Transport-Security"] = (
                f"max-age={self.hsts_max_age}; includeSubDomains"
            )

        return response


__all__ = ["SecurityHeadersMiddleware"]

"""Deprecated shim — SSRF protection moved to :mod:`core.security.ssrf`.

Kept for backward compatibility with existing imports; will be removed in a
future major release. ``WebhookSSRFError`` is now an alias of
:class:`core.security.ssrf.SsrfError`, so ``except WebhookSSRFError`` keeps
working for callers of the new API too.
"""

from __future__ import annotations

from core.security.ssrf import SsrfError as WebhookSSRFError
from core.security.ssrf import SsrfPolicy
from core.security.ssrf import resolve_pinned_target as _resolve

__all__ = ["WebhookSSRFError", "resolve_pinned_target", "validate_webhook_url"]


def resolve_pinned_target(url: str, *, allow_internal: bool = False) -> tuple[str, str]:
    """Deprecated: use :func:`core.security.ssrf.resolve_pinned_target`."""
    return _resolve(url, SsrfPolicy(allow_internal=allow_internal))


def validate_webhook_url(url: str, *, allow_internal: bool = False) -> None:
    """Deprecated: use :func:`core.security.ssrf.assert_url_safe`."""
    resolve_pinned_target(url, allow_internal=allow_internal)

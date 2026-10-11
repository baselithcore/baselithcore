"""Deprecated shim — SSRF protection moved to :mod:`baselith.core.security.ssrf`.

Deprecated since 1.0, removed in 2.0. Nothing in the framework imports this
module any more; it exists only so code written against the pre-1.0 webhook
API keeps importing. Migrate as follows:

=====================================================  ==========================================================================
Deprecated (this module)                               Replacement (:mod:`baselith.core.security.ssrf`)
=====================================================  ==========================================================================
``validate_webhook_url(url, allow_internal=x)``        ``assert_url_safe(url, SsrfPolicy(allow_internal=x))``
``resolve_pinned_target(url, allow_internal=x)``       ``resolve_pinned_target(url, SsrfPolicy(allow_internal=x))``
``WebhookSSRFError``                                   ``SsrfError``
=====================================================  ==========================================================================

The two functions warn on every call (``DeprecationWarning``, via
:func:`baselith.core.stability.deprecated`). ``WebhookSSRFError`` cannot: it is
not a wrapper but the very same class object as
:class:`~baselith.core.security.ssrf.SsrfError`, which is what keeps
``except WebhookSSRFError`` catching errors raised by the new API — and a class
that warned on use would warn on every ``SsrfError`` the framework raises. The
alias is therefore deprecated by documentation only and disappears with this
module in 2.0.
"""

from __future__ import annotations

from baselith.core.security.ssrf import SsrfError as WebhookSSRFError
from baselith.core.security.ssrf import SsrfPolicy, assert_url_safe
from baselith.core.security.ssrf import resolve_pinned_target as _resolve
from baselith.core.stability import deprecated

__all__ = ["WebhookSSRFError", "resolve_pinned_target", "validate_webhook_url"]


@deprecated(
    since="1.0",
    removed_in="2.0",
    alternative="baselith.core.security.ssrf.resolve_pinned_target",
)
def resolve_pinned_target(url: str, *, allow_internal: bool = False) -> tuple[str, str]:
    """Validate ``url`` and pin it to a verified external IP.

    Returns:
        ``(pinned_url, original_host)``, exactly as the replacement does.

    Raises:
        WebhookSSRFError: The URL is not a safe outbound target.
    """
    return _resolve(url, SsrfPolicy(allow_internal=allow_internal))


@deprecated(
    since="1.0",
    removed_in="2.0",
    alternative="baselith.core.security.ssrf.assert_url_safe",
)
def validate_webhook_url(url: str, *, allow_internal: bool = False) -> None:
    """Raise unless ``url`` is a safe outbound target.

    Raises:
        WebhookSSRFError: The URL is not a safe outbound target.
    """
    assert_url_safe(url, SsrfPolicy(allow_internal=allow_internal))

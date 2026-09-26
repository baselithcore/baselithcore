"""Late binding of the per-plugin LLM pin for services the funnel issues.

:func:`core.services.llm.runtime.get_llm_service` resolves the pin for the
plugin bound to the current context — at the moment it is called. A caller
that keeps the result (an agent built at plugin load, a flow handler, a
module-level client) used to keep that moment's decision forever: usually the
deployment default, because nothing is bound while plugins load, and never the
pin an operator set afterwards from the Policy LLM console.

Services the funnel issues are therefore marked, and each public entry point
re-resolves the pin **per call** and forwards the call to the service that pin
selects. The effect is the one an operator expects from the console: the pin
for whoever is calling decides, whenever they call, with no restart.

Only funnel-issued services are marked. A service built from an explicit
``LLMService(config=...)`` — a fallback stage, a plugin's own governed clone —
chose its target deliberately and is left alone, as documented for the
per-plugin policy.
"""

from __future__ import annotations

import inspect
from collections.abc import Callable
from functools import wraps
from typing import TYPE_CHECKING, Any, TypeVar

if TYPE_CHECKING:
    from core.services.llm.service import LLMService

__all__ = ["follows_policy", "governed_target", "mark_funnel_issued"]

_F = TypeVar("_F", bound=Callable[..., Any])

#: Attribute set on services issued by the funnel.
_MARK = "_funnel_issued"


def mark_funnel_issued(service: LLMService) -> LLMService:
    """Flag *service* as issued by the funnel, so its calls follow the pin."""
    setattr(service, _MARK, True)
    return service


def governed_target(service: Any) -> Any:
    """The service that should serve a call made through *service*, now.

    *service* itself unless it was issued by the funnel; then whatever the
    funnel would hand out in the current context — the pinned clone for the
    bound plugin (or a policy carried by a background job), else the default.
    Idempotent: the returned service resolves to itself.
    """
    if not getattr(service, _MARK, False):
        return service
    from core.services.llm.runtime import get_llm_service

    return get_llm_service()


def follows_policy(method: _F) -> _F:
    """Forward *method* to :func:`governed_target` when that is another service.

    Works for coroutine methods and async-generator methods alike.
    """
    name = method.__name__

    if inspect.isasyncgenfunction(method):

        @wraps(method)
        async def stream(self: Any, *args: Any, **kwargs: Any) -> Any:
            target = governed_target(self)
            source = (
                getattr(target, name)(*args, **kwargs)
                if target is not self
                else method(self, *args, **kwargs)
            )
            async for item in source:
                yield item

        return stream  # type: ignore[return-value]

    @wraps(method)
    async def call(self: Any, *args: Any, **kwargs: Any) -> Any:
        target = governed_target(self)
        if target is not self:
            return await getattr(target, name)(*args, **kwargs)
        return await method(self, *args, **kwargs)

    return call  # type: ignore[return-value]

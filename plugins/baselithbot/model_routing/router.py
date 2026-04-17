"""ModelRouter ties failover + auth rotation behind one ``invoke`` API."""

from __future__ import annotations

from typing import Any, Awaitable, Callable

from .auth_rotation import AuthProfile, AuthProfilePool
from .failover import FailoverPolicy, ProviderConfig, ProviderError

ProviderInvoker = Callable[
    [ProviderConfig, AuthProfile, dict[str, Any]], Awaitable[Any]
]


class ModelRouter:
    """Compose ``FailoverPolicy`` and ``AuthProfilePool`` into a single call."""

    def __init__(
        self,
        failover: FailoverPolicy,
        auth_pool: AuthProfilePool,
        invoker: ProviderInvoker,
    ) -> None:
        self._failover = failover
        self._auth = auth_pool
        self._invoker = invoker

    async def invoke(self, payload: dict[str, Any]) -> dict[str, Any]:
        async def _call(provider: ProviderConfig) -> Any:
            profile = self._auth.acquire()
            try:
                return await self._invoker(provider, profile, payload)
            except ProviderError:
                self._auth.report_error(profile.name)
                raise

        return await self._failover.call(_call)

    def status(self) -> dict[str, Any]:
        return {
            "providers": self._failover.status(),
            "auth_profiles": self._auth.status(),
        }


__all__ = ["ModelRouter", "ProviderInvoker"]

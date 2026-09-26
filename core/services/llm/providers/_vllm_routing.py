"""Per-model routing across several vLLM servers, for :class:`VLLMProvider`.

Split from ``vllm_provider`` (module size cap). The provider keeps one SDK
client per server and, on every call, asks
:class:`core.services.llm.vllm_endpoints.VLLMEndpointRegistry` which server
serves the requested model. The inherited OpenAI code paths build their client
through ``_ensure_client()``; a context variable tells it which server the
current call was routed to, so those paths need no change.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Any

from core.services.llm.exceptions import LLMProviderError
from core.services.llm.vllm_endpoints import get_vllm_registry

__all__ = ["VLLMRoutingMixin"]

#: The server the current (non-streaming) call was routed to.
_ACTIVE_BASE: ContextVar[str | None] = ContextVar("vllm_active_base", default=None)


class VLLMRoutingMixin:
    """Client-per-server and model routing; mixed into the vLLM provider."""

    _endpoints: list[str]
    _clients: dict[str, Any]
    _base_url: str | None
    client: Any

    @property
    def endpoints(self) -> list[str]:
        """Every configured server, in priority order."""
        return list(self._endpoints)

    def _routing_key(self) -> str | None:
        """The key a catalog probe sends (none for a keyless server)."""
        raise NotImplementedError

    async def _endpoint_for(self, model: str) -> str:
        """The server for *model*, or an error naming what is served.

        With one server there is nothing to decide. With several and no
        catalog at all (every server down) the first one is used, so the call
        itself reports the connection failure.

        Raises:
            LLMProviderError: No reachable server serves *model*.
        """
        if len(self._endpoints) == 1:
            return self._endpoints[0]
        registry = get_vllm_registry()
        key = self._routing_key()
        found = await registry.endpoint_for(model, self._endpoints, key)
        if found is not None:
            return found
        served = await registry.served(self._endpoints, key)
        if not served:
            return self._endpoints[0]
        models = sorted({m for names in served.values() for m in names})
        raise LLMProviderError(
            f"vLLM: model {model!r} is not served by any configured endpoint "
            f"(served: {', '.join(models)}). Pin one of those, or start a "
            f"server with --served-model-name {model} and add it to "
            "LLM_VLLM_ENDPOINTS."
        )

    @contextmanager
    def _routed(self, base: str) -> Iterator[None]:
        """Route the inherited code paths' client to *base* for one call."""
        token = _ACTIVE_BASE.set(base)
        try:
            yield
        finally:
            _ACTIVE_BASE.reset(token)

    def _client_for(self, base: str) -> Any:
        """The cached SDK client for *base*, built on first use."""
        client = self._clients.get(base)
        if client is None:
            saved_url, saved_client = self._base_url, self.client
            self._base_url, self.client = base, None
            try:
                client = super()._ensure_client()  # type: ignore[misc]
            finally:
                self._base_url, self.client = saved_url, saved_client
            self._clients[base] = client
        return client

    def _ensure_client(self) -> Any:
        """The client for the server the current call was routed to."""
        base = _ACTIVE_BASE.get() or self._endpoints[0]
        return self._client_for(base)

    async def close(self) -> None:
        """Close every server's client."""
        clients, self._clients = self._clients, {}
        for client in clients.values():
            try:
                await client.close()
            except Exception:  # silent-ok: shutdown is best effort
                pass

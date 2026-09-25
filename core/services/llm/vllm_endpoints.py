"""Several vLLM servers, routed by the model a call asks for.

A vLLM server serves one model (``vllm serve <model>``), so a deployment with
two models runs two servers, on two ports. Operators list them once::

    LLM_VLLM_ENDPOINTS=http://gpu:8002/v1,http://gpu:8003/v1

and every caller names a **model**, never a port. The registry learns which
models each server serves from its ``GET /v1/models`` catalog — the ids it
answers to, i.e. its ``--served-model-name`` — and hands back the server for a
model. A pin set in the Policy LLM console therefore survives a model moving to
another port, and a new model shows up without a restart.

Resolution rules:

* one endpoint → returned without any network call (the single-server
  deployment behaves exactly as before);
* the catalog is cached for ``ttl`` seconds; a model missing from it forces one
  refresh, at most every ``miss_refresh`` seconds, so a model just started on a
  server is found on the next call;
* a server that does not answer is skipped, never fatal — the others still
  route;
* a model served by two servers goes to the first one listed.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from core.observability.logging import get_logger
from core.services.llm._vllm_preflight import VLLMProbe, probe_vllm, probe_vllm_sync

if TYPE_CHECKING:
    from core.config.services import LLMConfig

logger = get_logger(__name__)

__all__ = [
    "VLLMEndpointRegistry",
    "get_vllm_registry",
    "split_endpoints",
    "vllm_endpoints",
]

#: Seconds a server's catalog is trusted before it is read again.
DEFAULT_TTL = 60.0
#: Minimum seconds between refreshes forced by a model missing from the catalog.
DEFAULT_MISS_REFRESH = 5.0
#: Probe deadline: short, because routing runs on the request path.
DEFAULT_TIMEOUT = 2.0


def split_endpoints(raw: str | None) -> list[str]:
    """The endpoints in a comma/whitespace-separated setting, normalised."""
    from core.services.llm.providers.vllm_provider import normalize_vllm_base_url

    out: list[str] = []
    for item in (raw or "").replace(",", " ").split():
        url = normalize_vllm_base_url(item)
        if url not in out:
            out.append(url)
    return out


def vllm_endpoints(config: LLMConfig) -> list[str]:
    """Every vLLM server this deployment routes to, in priority order.

    ``LLM_VLLM_ENDPOINTS`` first, then the single-server setting resolved by
    :func:`core.services.llm.runtime.api_base_for` (``LLM_VLLM_API_BASE``, or
    ``LLM_API_BASE`` when vLLM is the default provider), de-duplicated.
    """
    from core.services.llm.providers.vllm_provider import normalize_vllm_base_url

    endpoints = split_endpoints(getattr(config, "vllm_endpoints", None))
    single = _single_endpoint(config)
    if single:
        url = normalize_vllm_base_url(single)
        if url not in endpoints:
            endpoints.append(url)
    return endpoints


def _single_endpoint(config: LLMConfig) -> str | None:
    """The one-server setting, exactly as ``api_base_for`` resolved it before."""
    dedicated = getattr(config, "vllm_api_base", None)
    if dedicated:
        return str(dedicated)
    if getattr(config, "provider", None) == "vllm":
        base = getattr(config, "api_base", None)
        return str(base) if base else None
    return None


@dataclass
class _Entry:
    probe: VLLMProbe
    fetched_at: float = field(default_factory=time.monotonic)


class VLLMEndpointRegistry:
    """Which models each vLLM server serves, cached per server."""

    def __init__(
        self,
        ttl: float = DEFAULT_TTL,
        miss_refresh: float = DEFAULT_MISS_REFRESH,
        timeout: float = DEFAULT_TIMEOUT,
    ) -> None:
        self._ttl = ttl
        self._miss_refresh = miss_refresh
        self._timeout = timeout
        self._entries: dict[str, _Entry] = {}
        self._last_miss_refresh = float("-inf")
        self._lock = threading.Lock()

    def reset(self) -> None:
        """Forget every catalog (tests, a changed endpoint list)."""
        with self._lock:
            self._entries.clear()
            self._last_miss_refresh = float("-inf")

    # -- async (the provider, the preflight, the console) ---------------------

    async def endpoint_for(
        self, model: str, endpoints: list[str], api_key: str | None
    ) -> str | None:
        """The server that serves *model*, or ``None`` when none does.

        Args:
            model: The model id a call asks for.
            endpoints: The configured servers, in priority order.
            api_key: The key the servers expect, if any.

        Returns:
            The server's ``/v1`` root; the only one when a single server is
            configured; ``None`` when no reachable server serves *model*.
        """
        if len(endpoints) <= 1:
            return endpoints[0] if endpoints else None
        await self._refresh(endpoints, api_key, force=False)
        found = self._lookup(model, endpoints)
        if found is None and self._may_refresh_on_miss():
            await self._refresh(endpoints, api_key, force=True)
            found = self._lookup(model, endpoints)
        return found

    async def served(
        self, endpoints: list[str], api_key: str | None
    ) -> dict[str, set[str]]:
        """``endpoint -> models`` for every server that answered."""
        await self._refresh(endpoints, api_key, force=False)
        return self._reachable(endpoints)

    async def probes(
        self, endpoints: list[str], api_key: str | None
    ) -> dict[str, VLLMProbe]:
        """The latest probe of each server (fresh or cached)."""
        await self._refresh(endpoints, api_key, force=False)
        return {e: self._entries[e].probe for e in endpoints if e in self._entries}

    async def _refresh(
        self, endpoints: Iterable[str], api_key: str | None, *, force: bool
    ) -> None:
        for endpoint in endpoints:
            if force or self._stale(endpoint):
                probe = await probe_vllm(endpoint, api_key, timeout=self._timeout)
                self._store(endpoint, probe)

    # -- sync (plugins holding their own SDK resolve in sync code) ------------

    def endpoint_for_sync(
        self, model: str, endpoints: list[str], api_key: str | None
    ) -> str | None:
        """Synchronous :meth:`endpoint_for`, for sync call sites."""
        if len(endpoints) <= 1:
            return endpoints[0] if endpoints else None
        self._refresh_sync(endpoints, api_key, force=False)
        found = self._lookup(model, endpoints)
        if found is None and self._may_refresh_on_miss():
            self._refresh_sync(endpoints, api_key, force=True)
            found = self._lookup(model, endpoints)
        return found

    def _refresh_sync(
        self, endpoints: Iterable[str], api_key: str | None, *, force: bool
    ) -> None:
        for endpoint in endpoints:
            if force or self._stale(endpoint):
                self._store(
                    endpoint, probe_vllm_sync(endpoint, api_key, timeout=self._timeout)
                )

    # -- shared ----------------------------------------------------------------

    def _stale(self, endpoint: str) -> bool:
        entry = self._entries.get(endpoint)
        return entry is None or time.monotonic() - entry.fetched_at >= self._ttl

    def _store(self, endpoint: str, probe: VLLMProbe) -> None:
        previous = self._entries.get(endpoint)
        if (
            probe.status != "ok"
            and previous is not None
            and previous.probe.status == "ok"
        ):
            # Stale-while-error: keep routing to what the server last served
            # rather than losing its models on one failed probe.
            logger.warning(
                "vllm_catalog_probe_failed",
                extra={"endpoint": endpoint, "status": probe.status},
            )
            probe = previous.probe
        with self._lock:
            self._entries[endpoint] = _Entry(probe)

    def _lookup(self, model: str, endpoints: list[str]) -> str | None:
        hits = [
            e for e, models in self._reachable(endpoints).items() if model in models
        ]
        if len(hits) > 1:
            logger.warning(
                "vllm_model_on_several_endpoints",
                extra={"model": model, "endpoints": hits, "chosen": hits[0]},
            )
        return hits[0] if hits else None

    def _reachable(self, endpoints: list[str]) -> dict[str, set[str]]:
        return {
            e: set(self._entries[e].probe.models)
            for e in endpoints
            if e in self._entries and self._entries[e].probe.status == "ok"
        }

    def _may_refresh_on_miss(self) -> bool:
        now = time.monotonic()
        if now - self._last_miss_refresh < self._miss_refresh:
            return False
        self._last_miss_refresh = now
        return True


_registry = VLLMEndpointRegistry()


def get_vllm_registry() -> VLLMEndpointRegistry:
    """The process-wide registry."""
    return _registry

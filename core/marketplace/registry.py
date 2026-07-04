"""
Plugin Registry Client.

Handles discovery, searching, and metadata retrieval for plugins
from the marketplace registry (remote or local cache).
Standardized for Baselith Marketplace coherence.
"""

import logging
import os
from datetime import datetime, timedelta
from pathlib import Path
from urllib.parse import urlparse

import httpx

from core.config.plugins import PluginConfig
from core.marketplace.models import (
    MarketplacePlugin,
    PluginCategory,
    RegistryData,
)

# The SSRF guard lives in core.webhooks.ssrf (its first consumer) but is generic:
# it resolves the host and fails closed on any loopback/private/link-local/
# metadata address. The registry URL feeds the plugin installer (fetch → pip),
# so an internal-resolving URL here is a high-blast-radius SSRF.
from core.webhooks.ssrf import WebhookSSRFError, validate_webhook_url


def _env_true(name: str) -> bool:
    return os.environ.get(name, "").strip().lower() in ("1", "true", "yes", "on")


logger = logging.getLogger(__name__)


class PluginRegistry:
    """
    Registry client for marketplace plugin discovery.
    Coordinates local caching and remote fetching.
    """

    def __init__(self, config: PluginConfig | None = None):
        """Initialize registry client."""
        self.config = config or PluginConfig()
        self.cache_path = Path("cache/marketplace_registry.json")
        self._data: RegistryData | None = None
        # ID -> plugin index, rebuilt whenever registry data is (re)loaded so
        # get_plugin is O(1) instead of an O(n) scan over data.plugins.
        self._by_id: dict[str, MarketplacePlugin] = {}

    def _set_data(self, data: RegistryData) -> RegistryData:
        """Store registry data and rebuild the id->plugin lookup index."""
        self._data = data
        self._by_id = {p.id: p for p in data.plugins}
        return data

    async def fetch(self, force: bool = False) -> RegistryData:
        """
        Fetch the latest registry data from the remote URL.
        Use cache if available and not expired.
        """
        # 1. Check in-memory data
        if self._data and not force:
            return self._data

        # 2. Check local disk cache
        if self.cache_path.exists() and not force:
            try:
                mtime = datetime.fromtimestamp(self.cache_path.stat().st_mtime)
                if datetime.now() - mtime < timedelta(
                    seconds=self.config.registry_cache_ttl
                ):
                    with open(self.cache_path) as f:
                        data_json = f.read()
                        return self._set_data(
                            RegistryData.model_validate_json(data_json)
                        )
            except Exception as e:
                logger.warning(f"Error reading marketplace cache: {e}")

        # 3. Fetch remote
        logger.info(f"Fetching marketplace registry from {self.config.registry_url}")

        # Local file support for testing/air-gapped
        if self.config.registry_url.startswith("file://"):
            try:
                file_path = Path(self.config.registry_url.replace("file://", ""))
                with open(file_path) as f:
                    data_json = f.read()
                    data = self._set_data(RegistryData.model_validate_json(data_json))
                    self._save_to_cache(data_json)
                    return data
            except Exception as e:
                logger.error(f"Failed to read local marketplace registry: {e}")
                raise

        self._validate_registry_url(self.config.registry_url)

        async with httpx.AsyncClient(timeout=30.0) as client:
            try:
                response = await client.get(self.config.registry_url)
                response.raise_for_status()

                data_json = response.text
                data = self._set_data(RegistryData.model_validate_json(data_json))

                # Update cache
                self._save_to_cache(data_json)
                return data
            except Exception as e:
                logger.error(f"Failed to fetch marketplace registry: {e}")

                # Fallback to expired cache if fetch fails
                if self.cache_path.exists():
                    logger.info("Falling back to existing cache after fetch failure.")
                    with open(self.cache_path) as f:
                        data_json = f.read()
                        return self._set_data(
                            RegistryData.model_validate_json(data_json)
                        )
                raise RuntimeError(
                    f"Could not retrieve marketplace registry: {e}"
                ) from e

    @staticmethod
    def _validate_registry_url(url: str) -> None:
        """Reject unsafe marketplace registry URLs (MITM + SSRF).

        The registry feeds the plugin installer, so an attacker who controls the
        response can redirect installs to attacker-controlled packages. Two
        guards apply:

        * **Transport**: plaintext HTTP is refused (MITM tampering) except toward
          loopback hosts (local testing) or when explicitly opted in via
          ``BASELITH_MARKETPLACE_ALLOW_HTTP=true``.
        * **SSRF**: the host must not resolve to a loopback/private/link-local/
          cloud-metadata address — otherwise a mis/maliciously configured
          registry URL could pivot the server into the internal network (e.g.
          ``https://169.254.169.254/…``). Internal registries (air-gapped /
          on-prem artifact servers) opt in via
          ``BASELITH_MARKETPLACE_ALLOW_INTERNAL=true``.
        """
        parsed = urlparse(url)
        scheme = parsed.scheme.lower()
        if scheme not in ("http", "https"):
            raise ValueError(
                f"Unsupported marketplace registry scheme '{scheme}' in {url!r}; "
                "use https:// (or file:// for air-gapped registries)."
            )
        host = (parsed.hostname or "").lower()
        is_loopback = host in ("localhost", "127.0.0.1", "::1")

        if scheme == "http" and not is_loopback:
            if not _env_true("BASELITH_MARKETPLACE_ALLOW_HTTP"):
                raise ValueError(
                    f"Refusing plaintext HTTP marketplace registry {url!r}: plugin "
                    "metadata would be exposed to MITM tampering. Use https://, or "
                    "set BASELITH_MARKETPLACE_ALLOW_HTTP=true only on a trusted "
                    "network."
                )
            logger.warning(
                "Marketplace registry %s uses plaintext HTTP "
                "(BASELITH_MARKETPLACE_ALLOW_HTTP=true). Registry responses are "
                "not protected against tampering.",
                url,
            )

        # SSRF guard: fail closed on internal-resolving hosts. Loopback (local
        # testing) and explicit BASELITH_MARKETPLACE_ALLOW_INTERNAL bypass it.
        allow_internal = is_loopback or _env_true("BASELITH_MARKETPLACE_ALLOW_INTERNAL")
        try:
            validate_webhook_url(url, allow_internal=allow_internal)
        except WebhookSSRFError as e:
            raise ValueError(
                f"Refusing marketplace registry {url!r} (SSRF guard): {e}. Set "
                "BASELITH_MARKETPLACE_ALLOW_INTERNAL=true for a trusted internal "
                "registry."
            ) from e

    def _save_to_cache(self, content: str):
        """Persist registry data to disk."""
        try:
            self.cache_path.parent.mkdir(parents=True, exist_ok=True)
            with open(self.cache_path, "w") as f:
                f.write(content)
        except Exception as e:
            logger.warning(f"Failed to write marketplace cache: {e}")

    async def list_plugins(
        self,
        category: PluginCategory = PluginCategory.ALL,
        force: bool = False,
    ) -> list[MarketplacePlugin]:
        """List all plugins, optionally filtered by category."""
        data = await self.fetch(force=force)
        if category == PluginCategory.ALL:
            return data.plugins
        return [p for p in data.plugins if p.category == category]

    async def get_plugin(self, plugin_id: str) -> MarketplacePlugin | None:
        """Retrieve metadata for a specific plugin by ID."""
        await self.fetch()
        return self._by_id.get(plugin_id)

    async def search(
        self,
        query: str | None = None,
        category: PluginCategory = PluginCategory.ALL,
        force: bool = False,
    ) -> list[MarketplacePlugin]:
        """Search for plugins by text and category."""
        plugins = await self.list_plugins(category=category, force=force)
        if not query:
            return plugins

        query = query.lower()
        results = []
        for p in plugins:
            # Score matches
            score = 0
            if query in p.name.lower():
                score += 10
            if query in p.id.lower():
                score += 8
            if p.description and query in p.description.lower():
                score += 5
            if p.tags and any(query in t.lower() for t in p.tags):
                score += 3

            if score > 0:
                results.append((p, score))

        # Sort by best match
        results.sort(key=lambda x: x[1], reverse=True)
        return [r[0] for r in results]

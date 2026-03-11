"""
Plugin Registry Client.

Handles discovery, searching, and metadata retrieval for plugins
from the marketplace registry (remote or local cache).
Standardized for Baselith Marketplace coherence.
"""

import logging
from datetime import datetime, timedelta
from pathlib import Path
from typing import List, Optional

import httpx

from core.config.plugins import PluginConfig
from core.marketplace.models import (
    MarketplacePlugin,
    PluginCategory,
    RegistryData,
)

logger = logging.getLogger(__name__)


class PluginRegistry:
    """
    Registry client for marketplace plugin discovery.
    Coordinates local caching and remote fetching.
    """

    def __init__(self, config: Optional[PluginConfig] = None):
        """Initialize registry client."""
        self.config = config or PluginConfig()
        self.cache_path = Path("cache/marketplace_registry.json")
        self._data: Optional[RegistryData] = None

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
                    with open(self.cache_path, "r") as f:
                        data_json = f.read()
                        self._data = RegistryData.model_validate_json(data_json)
                        return self._data
            except Exception as e:
                logger.warning(f"Error reading marketplace cache: {e}")

        # 3. Fetch remote
        logger.info(f"Fetching marketplace registry from {self.config.registry_url}")

        # Local file support for testing/air-gapped
        if self.config.registry_url.startswith("file://"):
            try:
                file_path = Path(self.config.registry_url.replace("file://", ""))
                with open(file_path, "r") as f:
                    data_json = f.read()
                    self._data = RegistryData.model_validate_json(data_json)
                    self._save_to_cache(data_json)
                    return self._data
            except Exception as e:
                logger.error(f"Failed to read local marketplace registry: {e}")
                raise

        async with httpx.AsyncClient(timeout=30.0) as client:
            try:
                response = await client.get(self.config.registry_url)
                response.raise_for_status()

                data_json = response.text
                self._data = RegistryData.model_validate_json(data_json)

                # Update cache
                self._save_to_cache(data_json)
                return self._data
            except Exception as e:
                logger.error(f"Failed to fetch marketplace registry: {e}")

                # Fallback to expired cache if fetch fails
                if self.cache_path.exists():
                    logger.info("Falling back to existing cache after fetch failure.")
                    with open(self.cache_path, "r") as f:
                        data_json = f.read()
                        self._data = RegistryData.model_validate_json(data_json)
                        return self._data
                raise RuntimeError(f"Could not retrieve marketplace registry: {e}")

    def _save_to_cache(self, content: str):
        """Persist registry data to disk."""
        try:
            self.cache_path.parent.mkdir(parents=True, exist_ok=True)
            with open(self.cache_path, "w") as f:
                f.write(content)
        except Exception as e:
            logger.warning(f"Failed to write marketplace cache: {e}")

    async def list_plugins(
        self, category: PluginCategory = PluginCategory.ALL
    ) -> List[MarketplacePlugin]:
        """List all plugins, optionally filtered by category."""
        data = await self.fetch()
        if category == PluginCategory.ALL:
            return data.plugins
        return [p for p in data.plugins if p.category == category]

    async def get_plugin(self, plugin_id: str) -> Optional[MarketplacePlugin]:
        """Retrieve metadata for a specific plugin by ID."""
        data = await self.fetch()
        for p in data.plugins:
            if p.id == plugin_id:
                return p
        return None

    async def search(
        self, query: Optional[str] = None, category: PluginCategory = PluginCategory.ALL
    ) -> List[MarketplacePlugin]:
        """Search for plugins by text and category."""
        plugins = await self.list_plugins(category=category)
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

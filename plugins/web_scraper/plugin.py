"""Official Web Scraper plugin."""

from core.plugins import Plugin


class WebScraperPlugin(Plugin):
    """Plugin providing scraper and crawler capabilities."""

    async def initialize(self, config: dict) -> None:
        await super().initialize(config)

    async def shutdown(self) -> None:
        """Close the process-wide robots.txt client, then the plugin."""
        from ._http_pool import close_robots_client

        await close_robots_client()
        await super().shutdown()

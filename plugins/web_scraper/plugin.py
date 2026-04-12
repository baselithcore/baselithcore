"""Official Web Scraper plugin."""

from core.plugins import Plugin


class WebScraperPlugin(Plugin):
    """Plugin providing scraper and crawler capabilities."""

    async def initialize(self, config: dict) -> None:
        await super().initialize(config)

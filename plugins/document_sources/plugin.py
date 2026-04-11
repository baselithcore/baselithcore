"""Official Document Sources plugin."""

from core.plugins import Plugin


class DocumentSourcesPlugin(Plugin):
    """Plugin providing document ingestion sources."""

    async def initialize(self, config: dict) -> None:
        await super().initialize(config)

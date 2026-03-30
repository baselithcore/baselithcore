"""
BaselithCore plugin catalog exporters.

Provides integration adapters between the PluginRegistry and external
Software Catalog systems such as Backstage, Compass, and Port.

Public API
----------
    from core.plugins.exporters import BackstageProvider, backstage_exporter_router
    from core.plugins.exporters import set_backstage_provider
"""

from .backstage_provider import BackstageProvider
from .router import router as backstage_exporter_router, set_backstage_provider

__all__ = [
    "BackstageProvider",
    "backstage_exporter_router",
    "set_backstage_provider",
]

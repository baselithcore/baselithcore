"""
Export protocols for BaselithCore plugin catalog integrations.

Defines the structural contracts for exporting plugin metadata to external
Software Catalog systems. Using Python Protocols (PEP 544) enables
structural subtyping — future catalog backends (Compass, Port, OpsLevel)
can satisfy the contract without inheriting from a base class.

Usage
-----
    from core.plugins.protocols import BackstageExporter

    def register_exporter(exporter: BackstageExporter) -> None:
        assert isinstance(exporter, BackstageExporter)  # runtime check
        ...
"""

from __future__ import annotations

from typing import Any, Dict, List, Protocol, runtime_checkable


@runtime_checkable
class CatalogExporter(Protocol):
    """
    Generic protocol for Software Catalog exporters.

    Implement this interface to integrate BaselithCore's PluginRegistry
    with any Software Catalog system (Backstage, Compass, Port, OpsLevel…).

    All methods are async to satisfy the Async Everything dogma: export
    operations may involve I/O (HTTP pushes, file reads) and must not
    block the event loop.
    """

    async def export_entity(self, plugin: Any) -> Dict[str, Any]:
        """
        Serialize a single plugin instance to the catalog's entity schema.

        Args:
            plugin: An initialized Plugin instance.

        Returns:
            A dict representing one catalog entity (e.g., a catalog-info.yaml
            payload for Backstage or a Component object for Port).
        """
        ...

    async def export_all(self, registry: Any) -> List[Dict[str, Any]]:
        """
        Serialize all registered plugins to a list of catalog entities.

        Args:
            registry: The active PluginRegistry instance.

        Returns:
            A list of entity dicts, one per registered plugin.
        """
        ...

    async def get_provider_payload(self, registry: Any) -> Dict[str, Any]:
        """
        Build the full payload for an Entity Provider push / bulk upsert.

        Args:
            registry: The active PluginRegistry instance.

        Returns:
            A dict structured as the catalog backend's bulk-ingest contract
            (e.g., Backstage EntityProvider mutation, Port bulk-upsert body).
        """
        ...


@runtime_checkable
class BackstageExporter(CatalogExporter, Protocol):
    """
    Backstage-specific catalog exporter contract.

    Extends CatalogExporter with three Backstage-aware capabilities:

    1. ``to_catalog_info`` — generates a Backstage Component entity dict that
       serializes directly to a valid catalog-info.yaml (apiVersion v1alpha1).

    2. ``detect_agentic_patterns`` — inspects a plugin's source, tags, and
       resource declarations to identify which of the 22 BaselithCore Agentic
       Design Patterns it implements, returning them as Backstage label keys.

    3. ``get_health_status`` — maps the plugin's PluginState (from the
       PluginLifecycleManager) to a Backstage lifecycle string so the catalog
       reflects live operational health without manual updates.

    Swap guide
    ----------
    To replace Backstage with a different catalog, implement ``CatalogExporter``
    directly (or create a ``CompassExporter`` protocol extending it).  The
    calling code only depends on ``CatalogExporter``, so no callers change.
    """

    async def to_catalog_info(self, plugin: Any) -> Dict[str, Any]:
        """
        Map a plugin to a Backstage Component entity dict.

        The returned dict is valid YAML-serializable content for a
        catalog-info.yaml file (kind: Component, apiVersion: backstage.io/v1alpha1).

        Args:
            plugin: An initialized Plugin instance.

        Returns:
            A dict with keys: apiVersion, kind, metadata, spec.
        """
        ...

    async def detect_agentic_patterns(self, plugin: Any) -> List[str]:
        """
        Identify Agentic Design Patterns implemented by this plugin.

        Detection is performed asynchronously (source-file scanning runs in
        an executor thread to avoid blocking the event loop).

        Args:
            plugin: An initialized Plugin instance.

        Returns:
            A list of Backstage label keys, e.g.:
            ["baselith.ai/pattern-reasoning", "baselith.ai/pattern-reflection"]
        """
        ...

    async def get_health_status(self, plugin_name: str) -> str:
        """
        Map the plugin's current lifecycle state to a Backstage lifecycle string.

        Args:
            plugin_name: Unique name of the plugin as registered in the registry.

        Returns:
            One of: "production", "experimental", "deprecated", "unknown"
            (Backstage treats lifecycle as a freeform string; these values align
            with the official Backstage Well-Known Annotations spec.)
        """
        ...

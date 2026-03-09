"""Graph plugin interface for plugins that extend graph schema."""

from typing import Any, Dict, List

from .interface import Plugin


class GraphPlugin(Plugin):
    """
    Plugin that extends the graph database schema.

    Graph plugins can register custom entity types and relationship types
    that will be available in the graph database.
    """

    def register_entity_types(self) -> List[Dict[str, Any]]:
        """
        Register custom entity types for the graph database.

        Returns:
            List of entity type definitions, each with:
                - type: Entity type name (e.g., "story", "task")
                - display_name: Human-readable name
                - schema: Dictionary defining expected metadata fields
                - icon: Optional icon for UI display
        """
        return []

    def register_relationship_types(self) -> List[Dict[str, Any]]:
        """
        Register custom relationship types for the graph database.

        Returns:
            List of relationship type definitions, each with:
                - type: Relationship type name (e.g., "BLOCKS", "RELATES_TO")
                - source_types: List of valid source entity types
                - target_types: List of valid target entity types
                - properties_schema: Optional schema for relationship properties
                - bidirectional: Whether relationship is bidirectional
        """
        return []

    def get_entity_types(self) -> List[Dict[str, Any]]:
        """
        Get entity types provided by this plugin.

        Returns:
            List of entity type definitions
        """
        return self.register_entity_types()

    def get_relationship_types(self) -> List[Dict[str, Any]]:
        """
        Get relationship types provided by this plugin.

        Returns:
            List of relationship type definitions
        """
        return self.register_relationship_types()

    def validate_entity(self, entity_type: str, entity_data: Dict[str, Any]) -> bool:
        """
        Validate entity data against schema.

        Args:
            entity_type: Type of entity to validate
            entity_data: Entity data to validate

        Returns:
            True if valid, False otherwise
        """
        # Find entity type definition
        for entity_def in self.register_entity_types():
            if entity_def.get("type") == entity_type:
                schema = entity_def.get("schema", {})
                # Simple validation: check required fields exist
                for field, field_type in schema.items():
                    if field not in entity_data.get("metadata", {}):
                        return False
                return True
        return False

    def get_graph_config(self) -> Dict[str, Any]:
        """
        Get graph-specific configuration.

        Returns:
            Dictionary of graph configuration
        """
        return self.get_config("graph", {})

"""``resources/*`` handlers: concrete resources and parameterized templates.

A ``resources/read`` resolves against the concrete registry first and falls
back to the registered URI templates, so ``mcp://reports/2026/07`` is served by
whichever template declares ``mcp://reports/{year}/{month}``.
"""

from __future__ import annotations

from typing import Any

from core.mcp.errors import ResourceNotFound
from core.mcp.pagination import page_registry, with_cursor
from core.observability.logging import get_logger

logger = get_logger(__name__)


def _with_icons(entry: dict[str, Any], source: Any) -> dict[str, Any]:
    """Attach SEP-973 display icons when the primitive declares any."""
    icons = getattr(source, "icons", None)
    if icons:
        entry["icons"] = icons
    return entry


class ResourceHandlerMixin:
    """Mixin serving the resources primitive."""

    _resources: dict[str, Any]
    _resource_templates: dict[str, Any]
    config: Any

    async def _handle_list_resources(self, params: dict[str, Any]) -> dict[str, Any]:
        """Handle resources/list (concrete URIs only, paginated)."""
        page, next_cursor = page_registry(self._resources, params, self.config)
        resources = [
            _with_icons(
                {
                    "uri": res.uri,
                    "name": res.name,
                    "description": res.description,
                    "mimeType": res.mime_type,
                },
                res,
            )
            for res in page
        ]
        return with_cursor({"resources": resources}, next_cursor)

    async def _handle_list_resource_templates(
        self, params: dict[str, Any]
    ) -> dict[str, Any]:
        """Handle resources/templates/list — the parameterized resources."""
        page, next_cursor = page_registry(self._resource_templates, params, self.config)
        templates = [
            _with_icons(
                {
                    "uriTemplate": template.uri_template,
                    "name": template.name,
                    "description": template.description,
                    "mimeType": template.mime_type,
                },
                template,
            )
            for template in page
        ]
        return with_cursor({"resourceTemplates": templates}, next_cursor)

    def _resolve_resource(self, uri: str) -> tuple[Any, dict[str, str]]:
        """Find the resource or template serving *uri*.

        Returns:
            ``(resource_or_template, variables)`` — variables is empty for a
            concrete resource.

        Raises:
            ResourceNotFound: Nothing registered serves the URI.
        """
        from core.mcp.uri_template import match_template

        resource = self._resources.get(uri)
        if resource is not None:
            return resource, {}

        for template in self._resource_templates.values():
            variables = match_template(template.pattern, uri)
            if variables is not None:
                return template, variables

        raise ResourceNotFound(f"Unknown resource: {uri}")

    async def _handle_read_resource(self, params: dict[str, Any]) -> dict[str, Any]:
        """Handle resources/read for a concrete URI or a templated one."""
        uri = params.get("uri", "")
        resource, variables = self._resolve_resource(uri)
        if resource.handler is None:
            raise ResourceNotFound(f"Resource {uri} has no read handler")

        logger.info(f"MCP resource read: uri={uri}")

        content = await resource.handler(uri, **variables)

        return {
            "contents": [
                {
                    "uri": uri,
                    "mimeType": resource.mime_type,
                    "text": content,
                }
            ]
        }


__all__ = ["ResourceHandlerMixin"]

"""Registration API for :class:`~core.mcp.server.MCPServer`.

Everything a server owner declares — tools, resources, resource templates and
prompts — lands here, with the validation that has to happen at declaration
time rather than at call time: JSON Schema compilation, ``x-mcp-header``
constraints, and the URI-template matcher.
"""

from __future__ import annotations

import inspect
import re
from collections.abc import Callable, Coroutine
from typing import Any, get_type_hints

from core.observability.logging import get_logger

from .types import MCPPrompt, MCPResource, MCPResourceTemplate, MCPTool

logger = get_logger(__name__)

# SEP-986 tool-name guidance: 1-128 chars, ASCII letters, digits, `_`, `-`, `.`.
_TOOL_NAME = re.compile(r"^[A-Za-z0-9_.\-]{1,128}$")


def _warn_on_nonconforming_name(name: str) -> None:
    """Warn when a tool name departs from the SEP-986 guidance.

    A warning rather than an error: the rule is SHOULD, and rejecting would
    break servers that already registered such a name.
    """
    if not _TOOL_NAME.match(name):
        logger.warning(
            "mcp_tool_name_not_conforming",
            tool_name=name,
            hint="SEP-986: 1-128 chars of A-Z a-z 0-9 _ - .",
        )


class RegistrationMixin:
    """Declarative registration of every server primitive."""

    _tools: dict[str, MCPTool]
    _resources: dict[str, MCPResource]
    _resource_templates: dict[str, MCPResourceTemplate]
    _prompts: dict[str, MCPPrompt]
    # Supplied by MCPServer / SubscriptionHandlerMixin.
    _announce: Callable[[Any], None]
    notify_tools_changed: Callable[[], Any]
    notify_resources_changed: Callable[[], Any]
    notify_prompts_changed: Callable[[], Any]

    @staticmethod
    def _compile_schema(schema: dict[str, Any] | None) -> Any:
        """Compile *schema* once, so the hot path never re-parses it.

        SEP-1613 makes JSON Schema 2020-12 the default MCP dialect: a Draft-7
        validator silently ignores 2019-09+ keywords such as ``prefixItems``,
        so those constraints would go unenforced. An explicit ``$schema`` still
        wins via ``validator_for``.
        """
        if not isinstance(schema, dict) or not schema:
            return None
        from jsonschema.validators import Draft202012Validator, validator_for

        validator_cls = validator_for(schema, default=Draft202012Validator)
        return validator_cls(schema)

    def register_tool(
        self,
        name: str,
        description: str,
        input_schema: dict[str, Any],
        handler: Callable[..., Coroutine[Any, Any, Any]],
        category: str = "read_only",
        output_schema: dict[str, Any] | None = None,
        icons: list[dict[str, Any]] | None = None,
        long_running: bool = False,
    ) -> None:
        """
        Register a tool with the MCP server.

        Args:
            name: Unique tool name
            description: Human-readable description
            input_schema: JSON Schema for tool inputs
            handler: Async function to execute the tool
            category: Autonomy category (read_only | mutating | destructive |
                external_side_effect) consulted by the approval gate.
            output_schema: Optional JSON Schema for the tool's structured
                result. When set, the handler must return a mapping matching
                it and ``tools/call`` emits ``structuredContent``.
            icons: Optional display icons (SEP-973), each
                ``{"src", "mimeType", "sizes"}``.
            long_running: Return a durable task handle instead of blocking, for
                clients that declared the ``io.modelcontextprotocol/tasks``
                extension. Clients that did not still get the plain result.
        """
        # `x-mcp-header` constraints are enforced here, not at call time: a
        # tool advertised with an invalid annotation would be excluded by every
        # conforming client, so failing at registration is the honest moment.
        from core.mcp.param_headers import validate_annotations

        validate_annotations(input_schema)
        _warn_on_nonconforming_name(name)

        self._tools[name] = MCPTool(
            name=name,
            description=description,
            input_schema=input_schema,
            handler=handler,
            category=category,
            output_schema=output_schema,
            icons=icons,
            long_running=long_running,
            validator=self._compile_schema(input_schema),
            output_validator=self._compile_schema(output_schema),
        )
        logger.info("mcp_tool_registered", tool_name=name, category=category)
        self._announce(self.notify_tools_changed)

    def tool(
        self,
        name: str | None = None,
        description: str = "",
        input_schema: dict[str, Any] | None = None,
        category: str = "read_only",
        output_schema: dict[str, Any] | None = None,
    ) -> Callable[
        [Callable[..., Coroutine[Any, Any, Any]]],
        Callable[..., Coroutine[Any, Any, Any]],
    ]:
        """
        Decorator to register a function as an MCP tool.

        Usage:
            @server.tool(name="search", description="Search documents")
            async def search(query: str) -> list[dict]:
                ...
        """

        def decorator(
            func: Callable[..., Coroutine[Any, Any, Any]],
        ) -> Callable[..., Coroutine[Any, Any, Any]]:
            tool_name = name or func.__name__
            tool_description = description or func.__doc__ or ""

            # Auto-generate schema from function signature if not provided
            schema = input_schema or self._generate_schema_from_function(func)

            self.register_tool(
                tool_name,
                tool_description,
                schema,
                func,
                category=category,
                output_schema=output_schema,
            )
            return func

        return decorator

    def _generate_schema_from_function(
        self, func: Callable[..., Any]
    ) -> dict[str, Any]:
        """Generate JSON Schema from function type hints."""
        hints = get_type_hints(func) if hasattr(func, "__annotations__") else {}
        sig = inspect.signature(func)

        properties: dict[str, Any] = {}
        required: list[str] = []

        type_map = {
            str: "string",
            int: "integer",
            float: "number",
            bool: "boolean",
            list: "array",
            dict: "object",
        }

        for param_name, param in sig.parameters.items():
            if param_name in ("self", "cls"):
                continue

            param_type = hints.get(param_name, Any)
            json_type = type_map.get(param_type, "string")

            properties[param_name] = {"type": json_type}

            if param.default is inspect.Parameter.empty:
                required.append(param_name)

        return {
            "type": "object",
            "properties": properties,
            "required": required,
        }

    # -------------------------------------------------------------------------
    # Resource Registration
    # -------------------------------------------------------------------------

    def register_resource(
        self,
        uri: str,
        name: str,
        description: str,
        handler: Callable[..., Coroutine[Any, Any, str]],
        mime_type: str = "text/plain",
        icons: list[dict[str, Any]] | None = None,
    ) -> None:
        """
        Register a resource with the MCP server.

        Args:
            uri: Unique resource URI (e.g., mcp://docs/nav)
            name: Human-readable name
            description: Resource description
            handler: Async function to return resource content
            mime_type: Content MIME type
            icons: Optional display icons (SEP-973)
        """
        self._resources[uri] = MCPResource(
            uri=uri,
            name=name,
            description=description,
            mime_type=mime_type,
            handler=handler,
            icons=icons,
        )
        logger.info("mcp_resource_registered", uri=uri, name=name)
        self._announce(self.notify_resources_changed)

    def register_resource_template(
        self,
        uri_template: str,
        name: str,
        description: str,
        handler: Callable[..., Coroutine[Any, Any, str]],
        mime_type: str = "text/plain",
        icons: list[dict[str, Any]] | None = None,
        completions: dict[str, Any] | None = None,
    ) -> None:
        """
        Register a parameterized resource family.

        Args:
            uri_template: RFC 6570 Level-1 template, e.g.
                ``mcp://reports/{year}/{month}``. Each variable matches a
                single path segment.
            name: Human-readable name
            description: Template description
            handler: Async function called as ``handler(uri, **variables)``
            mime_type: Content MIME type of the produced resources
            icons: Optional display icons (SEP-973)
            completions: Per-variable ``completion/complete`` providers — a
                static list of candidates or a callable taking the partial
                value typed so far.
        """
        from core.mcp.uri_template import compile_template

        self._resource_templates[uri_template] = MCPResourceTemplate(
            uri_template=uri_template,
            name=name,
            description=description,
            mime_type=mime_type,
            handler=handler,
            icons=icons,
            completions=completions,
            pattern=compile_template(uri_template),
        )
        logger.info("mcp_resource_template_registered", uri_template=uri_template)
        self._announce(self.notify_resources_changed)

    def register_prompt(
        self,
        name: str,
        description: str,
        arguments: list[dict[str, Any]],
        handler: Callable[..., Coroutine[Any, Any, Any]],
        icons: list[dict[str, Any]] | None = None,
        completions: dict[str, Any] | None = None,
    ) -> None:
        """
        Register a prompt template.

        Args:
            name: Unique prompt name
            description: What the prompt is for
            arguments: Argument descriptors, each
                ``{"name", "description", "required"}``
            handler: Async function receiving the arguments as keywords and
                returning either a string (rendered as one user message) or an
                explicit list of ``PromptMessage`` dicts
            icons: Optional display icons (SEP-973)
            completions: Per-argument ``completion/complete`` providers — a
                static list of candidates or a callable taking the partial
                value typed so far.
        """
        self._prompts[name] = MCPPrompt(
            name=name,
            description=description,
            arguments=arguments,
            handler=handler,
            icons=icons,
            completions=completions,
        )
        logger.info("mcp_prompt_registered", prompt_name=name)
        self._announce(self.notify_prompts_changed)

    def resource(
        self,
        uri: str,
        name: str | None = None,
        description: str = "",
        mime_type: str = "text/plain",
    ) -> Callable[
        [Callable[..., Coroutine[Any, Any, str]]],
        Callable[..., Coroutine[Any, Any, str]],
    ]:
        """
        Decorator to register a function as an MCP resource provider.

        Usage:
            @server.resource(uri="mcp://config", name="App Config")
            async def get_config(uri: str) -> str:
                ...
        """

        def decorator(
            func: Callable[..., Coroutine[Any, Any, str]],
        ) -> Callable[..., Coroutine[Any, Any, str]]:
            res_name = name or func.__name__
            res_description = description or func.__doc__ or ""

            self.register_resource(uri, res_name, res_description, func, mime_type)
            return func

        return decorator

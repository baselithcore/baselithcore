"""
A2UI (Agent-to-User Interface) blueprint schema.

Implements the protocol from book ch8: agents emit JSON "blueprints" describing
a UI tree from a closed whitelist of components rather than raw HTML/JS. The
client renders the blueprint natively (web, mobile, desktop) so:

- Agents cannot inject ``<script>`` or other code paths.
- The same payload renders consistently across surfaces.
- Schema validation rejects unknown component types at the boundary.

Only the schema and validator live here; rendering is a UI concern.
"""

from __future__ import annotations

from enum import Enum
from typing import Annotated, Any, Final, Literal

from pydantic import BaseModel, ConfigDict, Field

A2UI_SCHEMA_VERSION: Final[str] = "a2ui/v1"
MAX_TREE_DEPTH: Final[int] = 16
MAX_TREE_NODES: Final[int] = 256


class ComponentType(str, Enum):
    """Whitelisted UI component types. Reject anything else."""

    CONTAINER = "container"
    TEXT = "text"
    HEADING = "heading"
    BUTTON = "button"
    LINK = "link"
    IMAGE = "image"
    INPUT = "input"
    FORM = "form"
    LIST = "list"
    LIST_ITEM = "list_item"
    DIVIDER = "divider"
    BADGE = "badge"


class BaseComponent(BaseModel):
    """Common base for any A2UI node."""

    model_config = ConfigDict(extra="forbid")

    id: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class Text(BaseComponent):
    type: Literal[ComponentType.TEXT] = ComponentType.TEXT
    content: str = Field(..., max_length=4096)


class Heading(BaseComponent):
    type: Literal[ComponentType.HEADING] = ComponentType.HEADING
    content: str = Field(..., max_length=512)
    level: int = Field(default=1, ge=1, le=6)


class Button(BaseComponent):
    type: Literal[ComponentType.BUTTON] = ComponentType.BUTTON
    label: str = Field(..., max_length=64)
    action: str = Field(..., max_length=128)
    payload: dict[str, Any] = Field(default_factory=dict)


class Link(BaseComponent):
    type: Literal[ComponentType.LINK] = ComponentType.LINK
    label: str = Field(..., max_length=128)
    href: str = Field(..., max_length=2048)


class Image(BaseComponent):
    type: Literal[ComponentType.IMAGE] = ComponentType.IMAGE
    src: str = Field(..., max_length=2048)
    alt: str = Field(default="", max_length=256)


class Input(BaseComponent):
    type: Literal[ComponentType.INPUT] = ComponentType.INPUT
    name: str = Field(..., max_length=64)
    label: str | None = Field(default=None, max_length=128)
    placeholder: str | None = Field(default=None, max_length=128)
    input_type: Literal["text", "number", "email", "password", "textarea"] = "text"
    required: bool = False


class ListItem(BaseComponent):
    type: Literal[ComponentType.LIST_ITEM] = ComponentType.LIST_ITEM
    children: list["Component"] = Field(default_factory=list)


class List(BaseComponent):
    type: Literal[ComponentType.LIST] = ComponentType.LIST
    items: list[ListItem] = Field(default_factory=list)


class Divider(BaseComponent):
    type: Literal[ComponentType.DIVIDER] = ComponentType.DIVIDER


class Badge(BaseComponent):
    type: Literal[ComponentType.BADGE] = ComponentType.BADGE
    label: str = Field(..., max_length=64)
    tone: Literal["neutral", "success", "warning", "danger", "info"] = "neutral"


class Form(BaseComponent):
    type: Literal[ComponentType.FORM] = ComponentType.FORM
    action: str = Field(..., max_length=128)
    children: list["Component"] = Field(default_factory=list)


class Container(BaseComponent):
    type: Literal[ComponentType.CONTAINER] = ComponentType.CONTAINER
    children: list["Component"] = Field(default_factory=list)


Component = Annotated[
    Container
    | Text
    | Heading
    | Button
    | Link
    | Image
    | Input
    | Form
    | List
    | ListItem
    | Divider
    | Badge,
    Field(discriminator="type"),
]


Container.model_rebuild()
Form.model_rebuild()
ListItem.model_rebuild()


class A2UIBlueprint(BaseModel):
    """Top-level envelope for an A2UI payload."""

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["a2ui/v1"] = "a2ui/v1"
    root: Component


class A2UIValidationError(ValueError):
    """Raised when a payload violates depth, size, or schema constraints."""


def _walk(node: Any) -> list[Any]:
    """Return the direct children of ``node`` regardless of variant."""
    if hasattr(node, "children") and isinstance(node.children, list):
        return list(node.children)
    if hasattr(node, "items") and isinstance(node.items, list):
        return list(node.items)
    return []


def _check_bounds(root: Any) -> None:
    """Walk the tree and enforce depth and node-count caps."""
    stack: list[tuple[Any, int]] = [(root, 1)]
    node_count = 0
    while stack:
        node, depth = stack.pop()
        node_count += 1
        if node_count > MAX_TREE_NODES:
            raise A2UIValidationError(f"blueprint exceeds {MAX_TREE_NODES} nodes")
        if depth > MAX_TREE_DEPTH:
            raise A2UIValidationError(f"blueprint exceeds depth {MAX_TREE_DEPTH}")
        for child in _walk(node):
            stack.append((child, depth + 1))


def validate_blueprint(payload: dict[str, Any]) -> A2UIBlueprint:
    """Parse and validate an A2UI payload. Raises on any violation."""
    blueprint = A2UIBlueprint.model_validate(payload)
    _check_bounds(blueprint.root)
    return blueprint

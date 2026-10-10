"""Value objects the MCP client hands back to callers."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from typing import Any

_HINTS = (
    ("readOnlyHint", "read_only_hint"),
    ("destructiveHint", "destructive_hint"),
    ("idempotentHint", "idempotent_hint"),
    ("openWorldHint", "open_world_hint"),
)


@dataclass(frozen=True)
class MCPToolAnnotations:
    """The behavioural hints a server attaches to a tool (spec ``ToolAnnotations``).

    The hints are **advisory and untrusted**: they come from the server being
    called, so a caller must never let ``read_only_hint`` lower the friction in
    front of a tool. ``None`` means the server did not say. A non-boolean value
    is treated as absent.

    Attributes:
        read_only_hint: The tool does not modify its environment.
        destructive_hint: The tool may perform destructive updates.
        idempotent_hint: Repeating a call with the same arguments is harmless.
        open_world_hint: The tool reaches entities outside the server.
        raw: The annotations object exactly as received (a copy), for fields
            this class does not model.
    """

    read_only_hint: bool | None = None
    destructive_hint: bool | None = None
    idempotent_hint: bool | None = None
    open_world_hint: bool | None = None
    raw: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_wire(cls, raw: Any) -> MCPToolAnnotations:
        """Parse a wire ``annotations`` value; anything malformed yields empty."""
        if not isinstance(raw, dict):
            return cls()
        hints = {
            attr: raw[key] if isinstance(raw.get(key), bool) else None
            for key, attr in _HINTS
        }
        return cls(**hints, raw=dict(raw))


def compute_spec_hash(
    name: str,
    description: str,
    input_schema: dict[str, Any],
    output_schema: dict[str, Any] | None,
) -> str:
    """SHA-256 over the canonical JSON of a tool's contract.

    The canonical form is the UTF-8 encoding of the JSON object
    ``{"description", "inputSchema", "name", "outputSchema"}`` serialised with
    sorted keys at every depth, no insignificant whitespace
    (``separators=(",", ":")``) and ``ensure_ascii=False``. A missing
    ``outputSchema`` is ``null``. The ``title`` and ``annotations`` are NOT
    part of the hash: they are presentation and advisory hints, while the four
    hashed fields are what the model is told and what the tool accepts, so a
    change in any of them is a change of contract (a "rug pull"). The result is
    the lowercase hex digest, 64 characters.
    """
    canonical = json.dumps(
        {
            "name": name,
            "description": description,
            "inputSchema": input_schema,
            "outputSchema": output_schema,
        },
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


@dataclass
class MCPToolInfo:
    """Information about an MCP tool.

    ``name``, ``description`` and ``input_schema`` keep their positional order;
    everything after is keyword-friendly and optional, so existing callers that
    build one by hand are unaffected.

    Attributes:
        title: Human-readable display name, when the server provided one.
        annotations: Advisory behavioural hints (see
            :class:`MCPToolAnnotations`).
        output_schema: JSON Schema of ``structuredContent``, if declared.
        spec_hash: :func:`compute_spec_hash` of the contract. Computed on
            construction when left empty.
    """

    name: str
    description: str
    input_schema: dict[str, Any]
    title: str | None = None
    annotations: MCPToolAnnotations = field(default_factory=MCPToolAnnotations)
    output_schema: dict[str, Any] | None = None
    spec_hash: str = ""

    def __post_init__(self) -> None:
        if not self.spec_hash:
            self.spec_hash = compute_spec_hash(
                self.name, self.description, self.input_schema, self.output_schema
            )

    @classmethod
    def from_wire(cls, raw: dict[str, Any]) -> MCPToolInfo:
        """Build from a ``tools/list`` entry. ``name`` must be a string."""
        description = raw.get("description")
        input_schema = raw.get("inputSchema")
        output_schema = raw.get("outputSchema")
        title = raw.get("title")
        return cls(
            name=raw["name"],
            description=description if isinstance(description, str) else "",
            input_schema=input_schema if isinstance(input_schema, dict) else {},
            title=title if isinstance(title, str) else None,
            annotations=MCPToolAnnotations.from_wire(raw.get("annotations")),
            output_schema=output_schema if isinstance(output_schema, dict) else None,
        )


__all__ = ["MCPToolAnnotations", "MCPToolInfo", "compute_spec_hash"]

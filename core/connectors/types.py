"""Value types of the connector contract: specs, results, health.

Everything here is a frozen dataclass on the standard library alone, so a
plugin can declare a connector's surface at import time without pulling in
the HTTP stack.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import Any

from core.orchestration._categories import UNDECLARED_DESTRUCTIVE

# Lower-case identifier with single underscores only: no ``__`` and no
# trailing ``_``. ``__`` is the separator in credential secret names
# (``CONNECTOR_<NAME>__<TENANT>__<FIELD>``), so allowing it here would let
# one connector's names collide with another's.
_NAME_RE = re.compile(r"^[a-z](?:[a-z0-9]|_(?=[a-z0-9])){0,63}$")

# The autonomy categories the approval gate understands (see
# ``core.reasoning.react_types.ToolDefinition``).
AUTONOMY_CATEGORIES = frozenset(
    {"read_only", "mutating", "destructive", "external_side_effect"}
)


class ConnectorCapability(StrEnum):
    """What a connector can do; each maps to one ``Supports*`` protocol."""

    LOOKUP = "lookup"
    SYNC = "sync"
    ACTION = "action"
    INBOUND = "inbound"


class HealthState(StrEnum):
    """Outcome of a connector health check."""

    OK = "ok"
    DEGRADED = "degraded"
    DOWN = "down"
    UNCONFIGURED = "unconfigured"


@dataclass(frozen=True, slots=True)
class CredentialField:
    """One credential a connector needs.

    Attributes:
        name: Field key, e.g. ``api_key``. Lower-case identifier, single
            underscores only.
        secret: Whether the value must be masked everywhere it is shown.
        required: Whether the connector is unconfigured without it.
        description: Human-readable hint for configuration UIs.
    """

    name: str
    secret: bool = True
    required: bool = True
    description: str = ""

    def __post_init__(self) -> None:
        if not _NAME_RE.match(self.name):
            raise ValueError(
                f"credential field {self.name!r} must match {_NAME_RE.pattern}"
            )


@dataclass(frozen=True, slots=True)
class ActionSpec:
    """A side-effecting operation a connector exposes.

    Attributes:
        name: Action key, unique within the connector.
        description: What the action does, shown to agents and operators.
        input_schema: JSON-Schema object describing the parameters.
        category: Autonomy category read by the approval gate. Defaults to
            the most restrictive one, so an undeclared action is gated; the
            default stays distinguishable as *undeclared*, so the typed
            ``Agent``'s standalone guard (which refuses only tools explicitly
            declared destructive) leaves it alone.
    """

    name: str
    description: str
    input_schema: dict[str, Any] = field(default_factory=dict)
    category: str = UNDECLARED_DESTRUCTIVE

    def __post_init__(self) -> None:
        if self.category not in AUTONOMY_CATEGORIES:
            raise ValueError(
                f"ActionSpec {self.name!r}: unknown category {self.category!r}"
            )


@dataclass(frozen=True, slots=True)
class ConnectorSpec:
    """Declarative description of a connector.

    Attributes:
        name: Registry key, lower-case identifier, single underscores only.
        display_name: Human-readable name.
        capabilities: The capabilities the connector implements.
        credentials: Credential fields it needs.
        actions: Actions it exposes (``action`` capability).
        allowed_hosts: Egress allowlist fed to the SSRF policy. ``None``
            allows any public host; prefer an explicit set.
        allow_global_fallback: Whether a tenant without its own credentials
            falls back to the deployment-wide ``CONNECTOR_<NAME>__<FIELD>``
            secret. Off, a tenant acts only through its own account.
        timeout_s: httpx per-phase timeout (connect, read, write, pool).
        deadline_s: Overall bound on one attempt, headers and body included.
            A per-phase read timeout never fires on a body that drips a byte
            at a time; this does. Must be at least ``timeout_s``.
        max_response_bytes: Largest response body accepted; a larger one
            is cut off and reported as :class:`ConnectorResponseTooLargeError`
            without being buffered. Connector output reaches agents, so a
            hostile or broken provider must not be able to exhaust a worker.
        max_attempts: Attempts per request, retries included.
        retry_base_delay: First backoff delay when the server gives none.
        retry_max_delay: Upper bound on any backoff, ``Retry-After`` included.
        vendor: Legal name of the ICT provider, for the DORA register.
        vendor_country: ISO 3166-1 alpha-2 country of the provider.
        vendor_lei: Legal Entity Identifier of the provider, when known.
    """

    name: str
    display_name: str
    capabilities: frozenset[ConnectorCapability] = frozenset()
    credentials: tuple[CredentialField, ...] = ()
    actions: tuple[ActionSpec, ...] = ()
    allowed_hosts: frozenset[str] | None = None
    allow_global_fallback: bool = True
    timeout_s: float = 30.0
    deadline_s: float = 120.0
    max_response_bytes: int = 8 * 1024 * 1024
    max_attempts: int = 3
    retry_base_delay: float = 0.5
    retry_max_delay: float = 30.0
    vendor: str | None = None
    vendor_country: str | None = None
    vendor_lei: str | None = None

    def __post_init__(self) -> None:
        if not _NAME_RE.match(self.name):
            raise ValueError(
                f"connector name {self.name!r} must match {_NAME_RE.pattern}"
            )
        if self.max_attempts < 1:
            raise ValueError("max_attempts must be >= 1")
        if self.max_response_bytes < 1:
            raise ValueError("max_response_bytes must be >= 1")
        if self.deadline_s < self.timeout_s:
            raise ValueError("deadline_s must be >= timeout_s")
        action_names = [a.name for a in self.actions]
        if len(set(action_names)) != len(action_names):
            raise ValueError(f"connector {self.name!r}: duplicate action names")
        if self.actions and ConnectorCapability.ACTION not in self.capabilities:
            raise ValueError(
                f"connector {self.name!r} declares actions without the "
                "'action' capability"
            )

    def action(self, name: str) -> ActionSpec | None:
        """Return the declared action called ``name``, if any."""
        return next((a for a in self.actions if a.name == name), None)


@dataclass(frozen=True, slots=True)
class ConnectorHealth:
    """Result of :meth:`Connector.health`."""

    state: HealthState
    detail: str = ""
    latency_ms: float | None = None

    @property
    def ok(self) -> bool:
        """True when the connector is fully usable."""
        return self.state is HealthState.OK


@dataclass(frozen=True, slots=True)
class SyncPage:
    """One page of an incremental sync.

    Attributes:
        items: Records in the page, normalised by the connector.
        next_cursor: Opaque cursor for the next page; ``None`` when done.
    """

    items: list[dict[str, Any]]
    next_cursor: str | None = None

    @property
    def has_more(self) -> bool:
        """Whether another page is available."""
        return self.next_cursor is not None


@dataclass(frozen=True, slots=True)
class ActionResult:
    """Outcome of an action.

    A business-level refusal (a declined charge, a rejected ticket) is a
    result with ``ok=False``; exceptions are reserved for failures to reach
    or talk to the external system.
    """

    ok: bool
    data: dict[str, Any] = field(default_factory=dict)
    error: str | None = None


@dataclass(frozen=True, slots=True)
class InboundEvent:
    """An event received from an external system, normalised."""

    type: str
    external_ref: str | None = None
    payload: dict[str, Any] = field(default_factory=dict)
    occurred_at: datetime | None = None


__all__ = [
    "AUTONOMY_CATEGORIES",
    "ActionResult",
    "ActionSpec",
    "ConnectorCapability",
    "ConnectorHealth",
    "ConnectorSpec",
    "CredentialField",
    "HealthState",
    "InboundEvent",
    "SyncPage",
]

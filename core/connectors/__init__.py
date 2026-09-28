"""Connector contract for integrations with external systems.

A connector declares its surface in a :class:`ConnectorSpec` (capabilities,
credentials, actions, egress allowlist, DORA vendor) and implements the
``Supports*`` protocols for the capabilities it has. Core supplies the
shared machinery around it: an SSRF-guarded, retrying, circuit-broken HTTP
client, one error hierarchy, credential resolution, a registry plugins
contribute to, an audited bridge that turns actions into agent and MCP
tools, and declaration of vendors into the DORA register.

Concrete connectors live in plugins; only the contract lives here.
"""

from core.connectors.base import BaseConnector
from core.connectors.credentials import (
    CredentialResolver,
    SecretsCredentialResolver,
    StaticCredentialResolver,
    credential_status,
    get_credential_resolver,
    set_credential_resolver,
)
from core.connectors.dora import declare_ict_providers, vendor_provider_id
from core.connectors.errors import (
    ConnectorAuthError,
    ConnectorConfigError,
    ConnectorConnectError,
    ConnectorEgressError,
    ConnectorError,
    ConnectorNotFoundError,
    ConnectorRateLimitedError,
    ConnectorRequestError,
    ConnectorTransientError,
    ConnectorUnavailableError,
)
from core.connectors.http import ConnectorHttp
from core.connectors.protocols import (
    Connector,
    SupportsAction,
    SupportsInbound,
    SupportsLookup,
    SupportsSync,
)
from core.connectors.registry import (
    ConnectorEntry,
    ConnectorFactory,
    ConnectorRegistry,
    create_connector,
    get_connector_registry,
    reset_connector_registry,
)
from core.connectors.tools import (
    connector_mcp_tools,
    connector_tool_definitions,
    invoke_action,
)
from core.connectors.types import (
    ActionResult,
    ActionSpec,
    ConnectorCapability,
    ConnectorHealth,
    ConnectorSpec,
    CredentialField,
    HealthState,
    InboundEvent,
    SyncPage,
)

__all__ = [
    # Types
    "ActionResult",
    "ActionSpec",
    "ConnectorCapability",
    "ConnectorHealth",
    "ConnectorSpec",
    "CredentialField",
    "HealthState",
    "InboundEvent",
    "SyncPage",
    # Errors
    "ConnectorAuthError",
    "ConnectorConfigError",
    "ConnectorConnectError",
    "ConnectorEgressError",
    "ConnectorError",
    "ConnectorNotFoundError",
    "ConnectorRateLimitedError",
    "ConnectorRequestError",
    "ConnectorTransientError",
    "ConnectorUnavailableError",
    # Protocols and base
    "BaseConnector",
    "Connector",
    "SupportsAction",
    "SupportsInbound",
    "SupportsLookup",
    "SupportsSync",
    # HTTP
    "ConnectorHttp",
    # Credentials
    "CredentialResolver",
    "SecretsCredentialResolver",
    "StaticCredentialResolver",
    "credential_status",
    "get_credential_resolver",
    "set_credential_resolver",
    # Registry
    "ConnectorEntry",
    "ConnectorFactory",
    "ConnectorRegistry",
    "create_connector",
    "get_connector_registry",
    "reset_connector_registry",
    # Tools
    "connector_mcp_tools",
    "connector_tool_definitions",
    "invoke_action",
    # DORA
    "declare_ict_providers",
    "vendor_provider_id",
]

---
title: Connectors
description: One contract for integrations with external systems — capabilities, egress, retries, credentials, audited tools, DORA
---

The `core/connectors` package is the contract every integration with an
external system implements: a CRM, a ticketing system, a threat-intel feed,
a payment provider. The package holds the contract and the machinery around
it. The concrete connectors live in plugins.

Without it, each integration rewrites the same concerns and gets some of them
wrong:

- the HTTP client lifecycle
- retrying on `429`/`5xx` while honouring `Retry-After`
- circuit breaking
- keeping tokens out of error messages
- deciding which hosts may be reached
- storing credentials per tenant
- auditing side effects

The contract supplies all of them once, built from pieces core already has:

| Concern | Built on |
|---|---|
| Egress allowlist, SSRF, DNS pinning | `core.security.http.create_hardened_async_client` + `SsrfPolicy(allowed_hosts=...)` |
| Retry with `Retry-After` | `core.resilience.retry` |
| Outage isolation | `core.resilience.get_circuit_breaker("connector.<name>")` |
| Credentials | `core.security.secrets` (env, `*_FILE`, secrets dir, Vault backends) |
| Registry | `core.registries.BaseRegistry` |
| Audit of actions | `core.observability.audit` (`TOOL_INVOKE`, with the actor) |
| Agent / MCP tools | `core.reasoning.react_types.ToolDefinition`, `Plugin.get_mcp_tools()` |
| DORA Art. 28 register | `core.thirdparty` |

The package is **experimental** (see [API Stability Tiers](../advanced/api-stability.md)).

## Capabilities

A connector declares what it can do. Each capability is a small
`runtime_checkable` protocol. Consumers test for a capability with
`isinstance` instead of trusting a flag:

| Capability | Protocol | Method |
|---|---|---|
| `lookup` | `SupportsLookup` | `async lookup(key) -> Mapping \| None` |
| `sync` | `SupportsSync` | `async sync(cursor=None, *, limit=None) -> SyncPage` |
| `action` | `SupportsAction` | `async invoke(action, params) -> ActionResult` |
| `inbound` | `SupportsInbound` | `verify(headers, body)` + `parse(headers, body) -> InboundEvent \| None` |

Every connector also satisfies `Connector`, meaning it has a `spec`,
`async health()` and `async aclose()`.

## Writing a connector

```python
from collections.abc import Mapping
from typing import Any

from core.connectors import (
    ActionResult,
    ActionSpec,
    BaseConnector,
    ConnectorCapability,
    ConnectorSpec,
    CredentialField,
    SyncPage,
)


class TicketsConnector(BaseConnector):
    spec = ConnectorSpec(
        name="tickets",
        display_name="Tickets",
        capabilities=frozenset(
            {ConnectorCapability.SYNC, ConnectorCapability.ACTION}
        ),
        credentials=(CredentialField("api_token"),),
        actions=(
            ActionSpec(
                "create_ticket",
                "Open a ticket",
                input_schema={
                    "type": "object",
                    "properties": {"title": {"type": "string"}},
                    "required": ["title"],
                },
                category="external_side_effect",
            ),
        ),
        allowed_hosts=frozenset({"api.tickets.example"}),
        vendor="Tickets Inc.",
        vendor_country="US",
    )

    def _auth(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.credential('api_token')}"}

    async def probe(self) -> None:
        await self.http.request(
            "GET", "https://api.tickets.example/me", headers=self._auth()
        )

    async def sync(self, cursor: str | None = None, *, limit: int | None = None) -> SyncPage:
        resp = await self.http.request(
            "GET",
            "https://api.tickets.example/tickets",
            params={"after": cursor or "", "limit": limit or 100},
            headers=self._auth(),
        )
        body = resp.json()
        return SyncPage(items=body["data"], next_cursor=body.get("next"))

    async def invoke(self, action: str, params: Mapping[str, Any]) -> ActionResult:
        resp = await self.http.request(
            "POST",
            "https://api.tickets.example/tickets",
            json=dict(params),
            headers=self._auth(),
        )
        return ActionResult(ok=True, data={"id": resp.json()["id"]})
```

`BaseConnector` is optional, since the protocols are the contract. What it
adds:

- **`self.http`**, a `ConnectorHttp` built on first use:
    - it can only reach `allowed_hosts`, redirect hops included;
    - it never reaches an internal address;
    - it applies `timeout_s`, `max_attempts`, `retry_base_delay` and
    `retry_max_delay` from the spec;
    - it bounds each attempt by `deadline_s` and each response body by
    `max_response_bytes` (see [Response limits](#response-limits));
    - it redacts the connector's secret credentials from every error message.
- **`credential(name)`** unwraps a credential when it is needed.
  `missing_credentials()` lists the required ones that are not set.
- **`health()`** returns one of four states:
    - `unconfigured` when a required credential is missing;
    - otherwise it runs `probe()` and times it, returning `ok`;
    - `down` if the probe is refused (`ConnectorAuthError` or
    `ConnectorConfigError`);
    - `degraded` on any other `ConnectorError`.
- **Lifecycle:** `aclose()` and `async with`. An injected `client=` is never
  closed.

An intermediate base without a spec declares itself with
`class MyBase(BaseConnector, abstract=True)`.

## Errors

Every failure is a `ConnectorError`, and its message is already redacted:

| Error | When | Retried |
|---|---|---|
| `ConnectorConfigError` | missing credential, undeclared action, unknown connector | no |
| `ConnectorAuthError` | 401 / 403 | no |
| `ConnectorNotFoundError` | 404 | no |
| `ConnectorRequestError` | any other 4xx (`.status`) | no |
| `ConnectorEgressError` | host outside `allowed_hosts`, or an internal address | no |
| `ConnectorTransientError` | transport error, timeout, 408, 5xx | idempotent requests only |
| `ConnectorConnectError` | the connection was never established | yes |
| `ConnectorRateLimitedError` | 429 (`.retry_after` from `Retry-After`) | yes, after the server's delay |
| `ConnectorUnavailableError` | circuit breaker open, so the call was not attempted | no |
| `ConnectorResponseTooLargeError` | response body larger than `max_response_bytes` | no — the same call returns the same body |

`ConnectorConnectError` and `ConnectorRateLimitedError` are both subclasses of
`ConnectorTransientError`. An attempt that does not complete within
`deadline_s` is a `ConnectorTransientError` too.

### Response limits

Connector output reaches agents, so a hostile or broken provider must not be
able to exhaust a worker. Three `ConnectorSpec` fields bound one attempt:

| Field | Default | Bound |
|---|---|---|
| `timeout_s` | `30.0` | httpx per-phase timeout (connect, read, write, pool) |
| `deadline_s` | `120.0` | The whole attempt, headers and body included. A per-phase read timeout never fires on a body that drips one byte at a time; this does. Must be at least `timeout_s`. |
| `max_response_bytes` | `8 * 1024 * 1024` (8 MiB) | The response body. A declared `Content-Length` above it is refused before reading; otherwise the body is streamed and cut off past the cap, never buffered. Must be at least `1`. |

The body is assembled into an ordinary buffered `httpx.Response`, so connector
code reads it as before; `Content-Encoding` and `Content-Length` are dropped
from that copy because the chunks were already decoded.

Only a genuine outage counts against the circuit breaker
(`connector.<name>`), meaning transport errors, timeouts, 408 and 5xx. A 404,
a rejected token or a rate limit can never open the circuit.

### Retries and idempotency

A request with an idempotent method (`GET`, `HEAD`, `OPTIONS`, `TRACE`, `PUT`,
`DELETE`) is retried on any transient failure, up to `max_attempts`.

Any other request (a `POST` that opens a ticket, a charge) is retried only when
the server provably did not act on it: a `429`, or a connection that was never
established. After a `5xx` or a read timeout the side effect may already have
happened, and repeating the request would double it, so the caller gets the
error instead.

A `POST` that is safe to repeat, such as a search endpoint or an API that takes
an idempotency key, opts in with `self.http.request("POST", url, idempotent=True)`.

A business-level refusal, such as a declined charge or a rejected ticket, is
an `ActionResult(ok=False, error=...)`, not an exception. Where a lookup
should fail open, the caller catches `ConnectorError`. The contract never
swallows it.

## Credentials

A `CredentialResolver` returns a connector's credentials for a tenant. The
default, `SecretsCredentialResolver`, reads them through
`core.security.secrets.get_secret` and tries the most specific name first:

```text
CONNECTOR_<NAME>__<TENANT>__<FIELD>   # per-tenant override
CONNECTOR_<NAME>__<FIELD>             # deployment-wide
```

So `CONNECTOR_TICKETS__API_TOKEN` configures every tenant, and
`CONNECTOR_TICKETS__ACME__API_TOKEN` overrides it for tenant `acme`.

The deployment-wide fallback is controlled by `ConnectorSpec.allow_global_fallback`
(default `True`). Set it to `False` for a connector that must act only through
each tenant's own account: inside a tenant only the per-tenant name is tried,
and a tenant without its own credentials reads as unconfigured. Out of a tenant
the deployment-wide name is still the only one there is.

The connector name and the field name are upper-cased. Both are identifiers
with single underscores only, since `__` is the separator. The tenant id is
encoded losslessly:

- lower-case letters and digits pass through, upper-cased;
- every other character becomes `_XX_`, where `XX` is its hex code.

So tenant `tenant-a` reads `CONNECTOR_TICKETS__TENANT_2D_A__API_TOKEN`, and
`tenant-a`, `tenant_a` and `Tenant-A` can never share credentials. Because
the lookup goes through the secrets provider, `*_FILE` files, a secrets
directory and a registered Vault backend all work, and the plugin secret guard
still applies.

A persistent store registers itself with `set_credential_resolver(...)`.
`StaticCredentialResolver` is for tests. `credential_status(spec, creds)`
returns a snapshot that is safe to show in a UI (`configured`, `missing`,
`set` per field) and never contains a value.

## Registry and construction

```python
from core.connectors import create_connector, get_connector_registry

get_connector_registry().register_connector(TicketsConnector)

# Credentials are resolved for the tenant bound in the current context.
connector = await create_connector("tickets")
async with connector:
    page = await connector.sync()
```

Plugins contribute connectors through the `Plugin.get_connectors()` hook. The
plugin registry records them with the plugin as owner and withdraws them when
the plugin unloads. A connector whose name is already taken, or an item that
is not a connector, is logged and skipped. It never blocks the plugin's
activation.

```python
class TicketsPlugin(Plugin):
    def get_connectors(self) -> list:
        return [TicketsConnector]
```

## Actions as agent and MCP tools

Every action runs through `invoke_action(connector, action, params)`:

- It refuses an action the spec does not declare.
- It records an audit event: `TOOL_INVOKE`, resource `connector:<name>`, the
  current user and tenant, and the outcome. Parameters are never logged,
  because they may carry personal data.

Two bridges adapt that path:

```python
from core.connectors import connector_mcp_tools, connector_tool_definitions

# ReAct agent tools, named "<connector>.<action>" like mounted MCP tools
tools = connector_tool_definitions(connector)

# The dict shape Plugin.get_mcp_tools() returns: expose on the core MCP server
def get_mcp_tools(self) -> list[dict]:
    return connector_mcp_tools(self.connector)
```

Both carry the action's autonomy `category`, so the approval gate applies. An
action that declares no category is gated as `destructive`. That default stays
recognisably *undeclared* (`core.orchestration._categories.UNDECLARED_DESTRUCTIVE`,
equal to the string `"destructive"` everywhere), so a standalone typed `Agent`
— which refuses only tools explicitly declared destructive (see
[Agent › Safe defaults](agent.md#safe-defaults-for-a-standalone-run)) — still
runs it. Write `category="destructive"` on the `ActionSpec` to have such an
agent refuse the action without an approval policy.

## DORA register

A connector that names a `vendor` is an ICT third-party dependency.
`declare_ict_providers()` adds one provider per distinct vendor to the
[ICT Third-Party Register](thirdparty.md):

```python
from core.connectors import declare_ict_providers

provider_ids = await declare_ict_providers()
```

Provider ids come from the vendor name (`vendor_provider_id`), so declaring
again finds the same row. An existing row is never modified, which keeps
whatever the operator filled in: expense, criticality, the contractual
arrangements. Declaring is an explicit call, not a side effect of
registration.

## Security contract

The machinery enforces what it can. The rest is on the connector author:

- **Mark every credential `secret=True`.** Redaction only masks the values of
  secret fields, and only through `BaseConnector` (or a `ConnectorHttp` built
  with `secrets=`). A token declared `secret=False`, or a signed nonce or
  session cookie the connector builds itself, is not redacted.
- **Keep credentials out of URLs.** Error messages quote the host, never the
  query string. Even so, send tokens in headers.
- **An injected `client=` must come from `create_hardened_async_client`.**
  Anything else is refused with `ValueError`, because it would bypass the SSRF
  guard. The injected client's own `SsrfPolicy` then replaces
  `spec.allowed_hosts`.
- **Never put a credential into `ActionResult.data`.** It is serialised
  verbatim into the agent's context and to MCP clients. Map the vendor's
  response to the fields the caller needs instead of echoing it.
  `ActionResult.error` is redacted by `invoke_action`, but `data` is not.
- **Declare `allowed_hosts`.** With `allowed_hosts=None` any public host is
  reachable (the SSRF guard still blocks internal addresses); in production
  the registry logs a WARNING naming the connector and its owner at
  registration.
- **Per-tenant secret names are dynamic**, so a plugin's
  `permissions.secrets` allowlist cannot list them one by one on a
  multi-tenant deployment. List the deployment-wide names, and know that the
  tenant overrides need a wildcard.

## When to use MCP instead

If the external system already publishes an MCP server, mount it through the
[MCP client](mcp.md) (`MCP_SERVERS`). No connector code is needed. Write a
connector when MCP is not enough:

- bulk or incremental sync;
- inbound webhooks;
- an on-premise or legacy system;
- per-tenant credentials the MCP server cannot take.

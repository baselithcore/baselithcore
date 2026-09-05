# Nodes

Manage remote **node pairing** — the WebSocket handshake that lets an
external process (an edge agent, a companion app) attach to this
BaselithBot instance.

## What the page does

- **Issue a pairing token** for a chosen platform label — one-shot,
  short-lived, rate-limited to 5/min.
- **Paired nodes** — search/filter by platform, sort by recency/platform/
  name; stat cards summarize totals.
- **Node detail drawer** with platform, pairing time, and identifying
  metadata.
- **Revoke** a paired node.

## Handshake

```text
POST /dash/nodes/token (🔒, 5/min)  → {"token": "..."}
WS   /baselithbot/ws/pair            client sends {token, node_id, platform}
                                      server replies {"status": "paired", ...}
```

A bad handshake closes the socket with code `4000`; exceeding the 20/min
handshake rate limit closes with `4290`. Once paired, the node can send text
frames and the server echoes `"ack: <first 200 chars>"` until disconnect —
real command routing (Connect / Chat / Voice families) is defined in the
node-commands module referenced from
[Architecture](../reference/architecture.md).

## Backend

`GET /dash/nodes`, `POST /dash/nodes/token` (🔒, 5/min),
`DELETE /dash/nodes/{node_id}` (🔒, 20/min — publishes `node.revoked`).
Issuing a token publishes `node.token_issued` on the SSE bus.

## Notes

Review paired nodes periodically — a stale or unexpected entry here is a
signal worth investigating; see the security checklist in
[Security & RBAC](../reference/security.md).

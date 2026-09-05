# Channels

Operational console for the 24 messaging channel adapters BaselithBot ships
— readiness, traffic, and credential health in one place.

## Adapters

- **First-party (4)** — Slack, Telegram, Discord, WebChat.
- **Generic webhook (20)** — WhatsApp, Google Chat, Signal, iMessage,
  BlueBubbles, IRC, Microsoft Teams, Matrix, Feishu, LINE, Mattermost,
  Nextcloud Talk, Nostr, Synology Chat, Tlon, Twitch, Zalo, WeChat, QQ, and a
  fully generic adapter for anything else.

All 24 are registered by `channels/bootstrap.py` into the plugin's
`ChannelRegistry` at startup.

## What the page does

- **Stats strip** — total, configured, live, and missing-credential counts,
  plus total inbound events across every channel.
- **Registry table** — search, filter by status (`all` / `live` /
  `configured` / `missing`), sort by traffic/name/status.
- **Detail drawer** per channel — edit its configuration (webhook URL,
  tokens, etc.), start/stop the live adapter, and send a test message.

## Backend

`GET /dash/channels` lists every registered adapter with live/config/traffic
state. Per-channel: `GET/PUT/DELETE /dash/channels/{name}/config` (write 🔒),
`POST /dash/channels/{name}/start` / `/stop` / `/test` (🔒). Secrets inside a
channel's `config` (webhook URLs, API keys) are redacted from structured
logs before they are ever written out.

## Inbound path

Every adapter receives events at `POST /baselithbot/inbound/{channel}`
(unauthenticated — providers can't present a bearer token): body capped at
1 MiB, parsed into a normalized `InboundEvent`
([`inbound/parsers.py`](../reference/architecture.md)), passed through
`DMPairingPolicy` (unpaired-sender DMs are denied — pair senders via the CLI,
see the [runbook](../operations/runbook.md)), counted on Prometheus per
channel, then fanned out by `InboundDispatcher` to registered handlers.

## Outbound

Programmatically, or via the MCP tool `baselithbot_channel_send`:

```python
await plugin.channels.send(
    ChannelMessage(channel="slack", target="#ops", text="…"),
    config={"webhook_url": "https://hooks.slack.com/…"},
)
```

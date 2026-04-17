"""Default channel registry covering every OpenClaw-supported channel.

First-party adapters (``WebChatAdapter``, ``SlackAdapter``,
``TelegramAdapter``, ``DiscordAdapter``) are wired directly. Every other
channel listed by OpenClaw is registered against ``GenericWebhookAdapter``
so it can deliver outbound messages once a ``webhook_url`` is provided in
its config block. Inbound delivery for non-first-party channels lives
outside the scope of this plugin (use the bridge daemons recommended by
each platform).
"""

from __future__ import annotations

from typing import Any, Callable, Final

from .base import ChannelAdapter
from .discord import DiscordAdapter
from .generic import GenericWebhookAdapter
from .irc import IRCAdapter
from .matrix import MatrixAdapter
from .microsoft_teams import MicrosoftTeamsAdapter
from .registry import ChannelRegistry
from .signal import SignalAdapter
from .slack import SlackAdapter
from .telegram import TelegramAdapter
from .twitch import TwitchAdapter
from .webchat import WebChatAdapter

SUPPORTED_CHANNELS: Final[tuple[str, ...]] = (
    "whatsapp",
    "telegram",
    "slack",
    "discord",
    "google_chat",
    "signal",
    "imessage",
    "bluebubbles",
    "irc",
    "microsoft_teams",
    "matrix",
    "feishu",
    "line",
    "mattermost",
    "nextcloud_talk",
    "nostr",
    "synology_chat",
    "tlon",
    "twitch",
    "zalo",
    "zalo_personal",
    "wechat",
    "qq",
    "webchat",
)


def _make_generic(name: str) -> Callable[[dict[str, Any]], ChannelAdapter]:
    def _factory(config: dict[str, Any]) -> ChannelAdapter:
        return GenericWebhookAdapter(name=name, config=config)

    return _factory


def build_default_registry() -> ChannelRegistry:
    """Return a ``ChannelRegistry`` with every supported channel registered."""
    registry = ChannelRegistry()

    registry.register("webchat", lambda cfg: WebChatAdapter(cfg))
    registry.register("slack", lambda cfg: SlackAdapter(cfg))
    registry.register("telegram", lambda cfg: TelegramAdapter(cfg))
    registry.register("discord", lambda cfg: DiscordAdapter(cfg))
    registry.register("matrix", lambda cfg: MatrixAdapter(cfg))
    registry.register("signal", lambda cfg: SignalAdapter(cfg))
    registry.register("irc", lambda cfg: IRCAdapter(cfg))
    registry.register("twitch", lambda cfg: TwitchAdapter(cfg))
    registry.register("microsoft_teams", lambda cfg: MicrosoftTeamsAdapter(cfg))

    first_party = {
        "webchat",
        "slack",
        "telegram",
        "discord",
        "matrix",
        "signal",
        "irc",
        "twitch",
        "microsoft_teams",
    }
    for channel in SUPPORTED_CHANNELS:
        if channel in first_party:
            continue
        registry.register(channel, _make_generic(channel))

    return registry


__all__ = ["build_default_registry", "SUPPORTED_CHANNELS"]

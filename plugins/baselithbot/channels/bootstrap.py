"""Default channel registry covering every OpenClaw-supported channel.

All 24 channels listed by OpenClaw are wired with first-party adapters.
``GenericWebhookAdapter`` remains exported for ad-hoc subscriptions outside
of ``SUPPORTED_CHANNELS``.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any, Final

from plugins.baselithbot.channels.base import ChannelAdapter
from plugins.baselithbot.channels.bluebubbles import BlueBubblesAdapter
from plugins.baselithbot.channels.discord import DiscordAdapter
from plugins.baselithbot.channels.feishu import FeishuAdapter
from plugins.baselithbot.channels.generic import GenericWebhookAdapter
from plugins.baselithbot.channels.google_chat import GoogleChatAdapter
from plugins.baselithbot.channels.imessage import IMessageAdapter
from plugins.baselithbot.channels.irc import IRCAdapter
from plugins.baselithbot.channels.line import LineAdapter
from plugins.baselithbot.channels.matrix import MatrixAdapter
from plugins.baselithbot.channels.mattermost import MattermostAdapter
from plugins.baselithbot.channels.microsoft_teams import MicrosoftTeamsAdapter
from plugins.baselithbot.channels.nextcloud_talk import NextcloudTalkAdapter
from plugins.baselithbot.channels.nostr import NostrAdapter
from plugins.baselithbot.channels.qq import QQAdapter
from plugins.baselithbot.channels.registry import ChannelRegistry
from plugins.baselithbot.channels.signal import SignalAdapter
from plugins.baselithbot.channels.slack import SlackAdapter
from plugins.baselithbot.channels.synology_chat import SynologyChatAdapter
from plugins.baselithbot.channels.telegram import TelegramAdapter
from plugins.baselithbot.channels.tlon import TlonAdapter
from plugins.baselithbot.channels.twitch import TwitchAdapter
from plugins.baselithbot.channels.webchat import WebChatAdapter
from plugins.baselithbot.channels.wechat import WeChatAdapter
from plugins.baselithbot.channels.whatsapp import WhatsAppAdapter
from plugins.baselithbot.channels.zalo import ZaloAdapter, ZaloPersonalAdapter

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
    registry.register("mattermost", lambda cfg: MattermostAdapter(cfg))
    registry.register("whatsapp", lambda cfg: WhatsAppAdapter(cfg))
    registry.register("google_chat", lambda cfg: GoogleChatAdapter(cfg))
    registry.register("bluebubbles", lambda cfg: BlueBubblesAdapter(cfg))
    registry.register("imessage", lambda cfg: IMessageAdapter(cfg))
    registry.register("feishu", lambda cfg: FeishuAdapter(cfg))
    registry.register("line", lambda cfg: LineAdapter(cfg))
    registry.register("nextcloud_talk", lambda cfg: NextcloudTalkAdapter(cfg))
    registry.register("nostr", lambda cfg: NostrAdapter(cfg))
    registry.register("synology_chat", lambda cfg: SynologyChatAdapter(cfg))
    registry.register("tlon", lambda cfg: TlonAdapter(cfg))
    registry.register("zalo", lambda cfg: ZaloAdapter(cfg))
    registry.register("zalo_personal", lambda cfg: ZaloPersonalAdapter(cfg))
    registry.register("wechat", lambda cfg: WeChatAdapter(cfg))
    registry.register("qq", lambda cfg: QQAdapter(cfg))

    first_party = set(SUPPORTED_CHANNELS)
    for channel in SUPPORTED_CHANNELS:
        if channel in first_party:
            continue
        registry.register(channel, _make_generic(channel))

    return registry


__all__ = ["build_default_registry", "SUPPORTED_CHANNELS"]

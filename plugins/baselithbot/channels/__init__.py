"""Multi-channel inbox adapters (OpenClaw parity).

Provides a uniform ``ChannelAdapter`` ABC plus a registry that ships with
adapters for every messaging channel listed by OpenClaw. Channels with no
first-party SDK fall back to ``GenericWebhookAdapter`` so they can still
deliver outbound messages via a configured HTTP endpoint.
"""

from plugins.baselithbot.channels.base import (
    ChannelAdapter,
    ChannelMessage,
    ChannelStatus,
    validate_https_url,
)
from plugins.baselithbot.channels.bootstrap import SUPPORTED_CHANNELS, build_default_registry
from plugins.baselithbot.channels.registry import ChannelRegistry

__all__ = [
    "ChannelAdapter",
    "ChannelMessage",
    "ChannelStatus",
    "ChannelRegistry",
    "build_default_registry",
    "SUPPORTED_CHANNELS",
    "validate_https_url",
]

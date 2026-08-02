"""Unit tests for the Baselithbot plugin — channel registry and adapters."""

from __future__ import annotations

import pytest


def test_default_channel_registry_lists_24_openclaw_channels() -> None:
    from plugins.baselithbot.channels import build_default_registry

    registry = build_default_registry()
    known = registry.known()
    assert len(known) == 24
    for required in ("whatsapp", "telegram", "slack", "discord", "webchat"):
        assert required in known


@pytest.mark.asyncio
async def test_webchat_adapter_round_trip() -> None:
    from plugins.baselithbot.channels import ChannelMessage
    from plugins.baselithbot.channels.webchat import WebChatAdapter

    adapter = WebChatAdapter()
    await adapter.startup()
    out = await adapter.send(
        ChannelMessage(channel="webchat", target="user-1", text="hi")
    )
    assert out["status"] == "success"
    history = await adapter.history()
    assert history[-1]["text"] == "hi"


def test_extra_channel_adapters_registered() -> None:
    from plugins.baselithbot.channels import build_default_registry

    known = set(build_default_registry().known())
    assert {"matrix", "signal", "irc", "twitch", "microsoft_teams"} <= known


@pytest.mark.asyncio
async def test_matrix_adapter_unconfigured() -> None:
    from plugins.baselithbot.channels import ChannelMessage
    from plugins.baselithbot.channels.matrix import MatrixAdapter

    adapter = MatrixAdapter()
    out = await adapter.send(
        ChannelMessage(channel="matrix", target="!room:example.org", text="hi")
    )
    assert out["status"] == "unconfigured"


def test_all_24_channels_have_first_party_adapter() -> None:
    from plugins.baselithbot.channels import (
        SUPPORTED_CHANNELS,
        build_default_registry,
    )
    from plugins.baselithbot.channels.generic import GenericWebhookAdapter

    registry = build_default_registry()
    for channel in SUPPORTED_CHANNELS:
        adapter = registry._factories[channel]({})  # type: ignore[attr-defined]
        assert not isinstance(adapter, GenericWebhookAdapter), (
            f"channel '{channel}' still using generic webhook fallback"
        )


@pytest.mark.asyncio
async def test_mattermost_adapter_unconfigured_returns_marker() -> None:
    from plugins.baselithbot.channels import ChannelMessage
    from plugins.baselithbot.channels.mattermost import MattermostAdapter

    adapter = MattermostAdapter()
    out = await adapter.send(
        ChannelMessage(channel="mattermost", target="general", text="hi")
    )
    assert out["status"] == "unconfigured"


@pytest.mark.asyncio
async def test_whatsapp_adapter_unconfigured_lists_missing_creds() -> None:
    from plugins.baselithbot.channels import ChannelMessage
    from plugins.baselithbot.channels.whatsapp import WhatsAppAdapter

    out = await WhatsAppAdapter().send(
        ChannelMessage(channel="whatsapp", target="+39000", text="hi")
    )
    assert out["status"] == "unconfigured"
    assert {"access_token", "phone_number_id"} <= set(out["missing"])


def test_channel_https_url_validation_rejects_http() -> None:
    from plugins.baselithbot.channels import ChannelMessage
    from plugins.baselithbot.channels.slack import SlackAdapter

    adapter = SlackAdapter({"webhook_url": "http://hooks.slack.com/xyz"})
    assert adapter.is_configured() is False

    adapter_https = SlackAdapter({"webhook_url": "https://hooks.slack.com/xyz"})
    assert adapter_https.is_configured() is True

    adapter_local = SlackAdapter({"webhook_url": "http://localhost:8000/hook"})
    assert adapter_local.is_configured() is True
    del ChannelMessage  # silence unused import

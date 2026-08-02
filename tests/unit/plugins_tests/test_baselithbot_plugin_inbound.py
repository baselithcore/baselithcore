"""Unit tests for the Baselithbot plugin — inbound, policies, signatures, redaction."""

from __future__ import annotations

from typing import Any

import pytest


@pytest.mark.asyncio
async def test_inbound_dispatcher_runs_handler() -> None:
    from plugins.baselithbot.inbound import InboundDispatcher, InboundEvent

    disp = InboundDispatcher()
    received: list[InboundEvent] = []

    async def handler(event: InboundEvent) -> dict[str, Any]:
        received.append(event)
        return {"status": "ok", "echo": event.text}

    disp.register("slack", handler)
    out = await disp.dispatch(InboundEvent(channel="slack", sender="alice", text="hi"))
    assert out and out[0]["status"] == "ok"
    assert received[0].text == "hi"
    assert disp.stats() == {"slack": 1}


def test_inbound_parsers_normalize_payloads() -> None:
    from plugins.baselithbot.inbound.parsers import (
        parse_discord_interaction,
        parse_slack_event,
        parse_telegram_update,
    )

    s = parse_slack_event({"event": {"user": "U1", "text": "hi"}})
    assert s.channel == "slack" and s.sender == "U1"

    t = parse_telegram_update(
        {"message": {"from": {"username": "alice"}, "text": "hello"}}
    )
    assert t.channel == "telegram" and t.sender == "alice" and t.text == "hello"

    d = parse_discord_interaction(
        {"member": {"user": {"username": "bob"}}, "data": {"name": "ping"}}
    )
    assert d.channel == "discord" and d.sender == "bob" and d.text == "ping"


def test_dm_policy_blocks_unallowlisted_sender_and_rate_limits() -> None:
    from plugins.baselithbot.policies import DMPairingPolicy

    policy = DMPairingPolicy()
    policy.configure(
        "telegram",
        allowed_senders=["alice"],
        rate_limit_window_s=60.0,
        rate_limit_max_events=2,
    )
    assert policy.evaluate("telegram", "alice").allowed is True
    assert policy.evaluate("telegram", "alice").allowed is True
    assert policy.evaluate("telegram", "alice").allowed is False
    blocked = policy.evaluate("telegram", "mallory")
    assert blocked.allowed is False
    assert "allowlist" in blocked.reason


def test_host_acl_default_and_rules() -> None:
    from plugins.baselithbot.policies import HostACL, HostACLRule

    acl = HostACL(default="deny")
    assert acl.decide("mouse_click") is False
    acl.add(HostACLRule(name="allow-clicks", action="mouse_click", decision="allow"))
    assert acl.decide("mouse_click", {"x": 10}) is True
    acl.add(
        HostACLRule(
            name="deny-fs",
            action="fs_write",
            pattern=r"/etc/",
            decision="deny",
        )
    )
    assert acl.decide("fs_write", {"path": "/etc/passwd"}) is False


def test_slack_signature_verifier_round_trip() -> None:
    import hashlib
    import hmac

    from plugins.baselithbot.inbound import verify_slack_signature

    secret = "shh"
    body = b'{"event": "x"}'
    timestamp = "1700000000"
    base = f"v0:{timestamp}:".encode() + body
    digest = hmac.new(secret.encode("utf-8"), base, hashlib.sha256).hexdigest()
    sig = f"v0={digest}"

    assert verify_slack_signature(secret, timestamp, body, sig) is True
    assert verify_slack_signature(secret, timestamp, body, "v0=bad") is False
    assert verify_slack_signature(secret, timestamp, body, "wrong-prefix") is False


def test_github_signature_verifier_rejects_mismatch() -> None:
    import hashlib
    import hmac

    from plugins.baselithbot.inbound import verify_github_signature

    secret = "topsecret"
    body = b"payload"
    digest = hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()
    assert verify_github_signature(secret, body, f"sha256={digest}") is True
    assert verify_github_signature(secret, body, "sha256=deadbeef") is False
    assert verify_github_signature(secret, body, "no-prefix") is False


def test_telegram_secret_token_verifier() -> None:
    from plugins.baselithbot.inbound import verify_telegram_secret_token

    assert verify_telegram_secret_token("abc", "abc") is True
    assert verify_telegram_secret_token("abc", "xyz") is False
    assert verify_telegram_secret_token("abc", None) is False


def test_secret_redaction_masks_known_keys() -> None:
    from plugins.baselithbot.security.redaction import redact_payload

    out = redact_payload(
        {
            "bot_token": "1234567890abcdef",
            "webhook_url": "https://hooks.example/xyz/AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA",
            "text": "hello",
            "nested": {"api_key": "k", "ok": "fine"},
        }
    )
    assert out["bot_token"] == "<redacted>"
    assert out["webhook_url"] == "<redacted>"
    assert out["text"] == "hello"
    assert out["nested"]["api_key"] == "<redacted>"
    assert out["nested"]["ok"] == "fine"


def test_secret_redaction_masks_long_tokens_in_strings() -> None:
    from plugins.baselithbot.security.redaction import redact_payload

    raw = "Authorization: Bearer abcdef1234567890abcdef1234567890abcdef"
    out = redact_payload(raw)
    assert "Bearer <redacted>" in out


@pytest.mark.asyncio
async def test_inbound_dispatcher_runs_handlers_in_parallel() -> None:
    import asyncio as _asyncio
    import time as _time

    from plugins.baselithbot.inbound import InboundDispatcher, InboundEvent

    disp = InboundDispatcher()

    async def slow_a(event):
        del event
        await _asyncio.sleep(0.1)
        return {"who": "a"}

    async def slow_b(event):
        del event
        await _asyncio.sleep(0.1)
        return {"who": "b"}

    disp.register("multi", slow_a)
    disp.register("multi", slow_b)
    started = _time.time()
    out = await disp.dispatch(InboundEvent(channel="multi", text=""))
    elapsed = _time.time() - started
    assert {r["who"] for r in out} == {"a", "b"}
    assert elapsed < 0.18, f"handlers ran sequentially: {elapsed:.3f}s"

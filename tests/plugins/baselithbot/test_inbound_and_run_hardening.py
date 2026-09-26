"""Security regressions: inbound webhook surface, dashboard auth, replay tenancy.

Each test pins one defect found in the 2026-09 baselithbot audit:

* the ``dm_policy`` section of ``plugins.yaml`` was never applied, so the
  documented sender allowlist let every sender through;
* the inbound route buffered the whole body before its 413 check;
* a channel spelled ``Slack`` dodged a policy configured for ``slack``;
* Discord interactions had no replay window;
* ``compare_digest`` on non-ASCII header text raised (500 instead of 401/403);
* a client-chosen ``run_id`` let one tenant re-file and read another's run.
"""

from __future__ import annotations

import hashlib
import hmac
import time
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from plugins.baselithbot import BaselithbotPlugin

_TOKEN = "test-dashboard-token"
_GENERIC_SECRET = "generic-inbound-secret"


def _client(monkeypatch: pytest.MonkeyPatch, state_dir: str) -> TestClient:
    monkeypatch.setenv("BASELITHBOT_DASHBOARD_TOKEN", _TOKEN)
    monkeypatch.setenv("BASELITHBOT_INBOUND_GENERIC_SECRET", _GENERIC_SECRET)
    monkeypatch.delenv("BASELITHBOT_INBOUND_INSECURE", raising=False)
    plugin = BaselithbotPlugin(state_dir=state_dir)
    app = FastAPI()
    app.include_router(plugin.create_router(), prefix="/baselithbot")
    client = TestClient(app, raise_server_exceptions=False)
    client._plugin = plugin  # type: ignore[attr-defined]
    return client


def _signed(body: bytes) -> dict[str, str]:
    digest = hmac.new(_GENERIC_SECRET.encode(), body, hashlib.sha256).hexdigest()
    return {
        "X-Baselithbot-Signature": f"sha256={digest}",
        "Content-Type": "application/json",
    }


# ---------------------------------------------------------------- dm_policy


def test_dm_policy_section_is_enforced_after_initialize(state_dir: str) -> None:
    import asyncio

    plugin = BaselithbotPlugin(state_dir=state_dir)
    config = {"dm_policy": {"WhatsApp": {"allowed_senders": [12345]}}}

    async def _init() -> None:
        await plugin.initialize(config)
        await plugin.shutdown()

    asyncio.run(_init())
    # Numeric YAML ids are normalised to strings; channel keys to lowercase.
    assert plugin.dm_policy.evaluate("whatsapp", "12345").allowed
    denied = plugin.dm_policy.evaluate("whatsapp", "intruder")
    assert not denied.allowed
    assert "allowlist" in denied.reason


def test_dm_policy_rejects_malformed_section() -> None:
    from plugins.baselithbot.policies import DMPairingPolicy

    with pytest.raises(ValueError):
        DMPairingPolicy().configure_from_mapping(["slack"])
    with pytest.raises(ValueError):
        DMPairingPolicy().configure_from_mapping({"slack": "U1"})


def test_inbound_channel_case_cannot_bypass_allowlist(
    monkeypatch: pytest.MonkeyPatch, state_dir: str
) -> None:
    client = _client(monkeypatch, state_dir)
    plugin = client._plugin  # type: ignore[attr-defined]
    plugin.dm_policy.configure("whatsapp", allowed_senders=["friend"])
    body = b'{"sender": "intruder", "text": "run rm -rf"}'
    for spelling in ("whatsapp", "WhatsApp", "WHATSAPP"):
        resp = client.post(
            f"/baselithbot/inbound/{spelling}", content=body, headers=_signed(body)
        )
        assert resp.status_code == 200
        assert resp.json()["status"] == "denied", spelling


def test_inbound_unknown_channel_is_404_before_reading_body(
    monkeypatch: pytest.MonkeyPatch, state_dir: str
) -> None:
    client = _client(monkeypatch, state_dir)
    body = b'{"text": "hi"}'
    resp = client.post(
        "/baselithbot/inbound/no-such-channel", content=body, headers=_signed(body)
    )
    assert resp.status_code == 404


# ---------------------------------------------------------------- body cap


def test_inbound_rejects_oversized_declared_length(
    monkeypatch: pytest.MonkeyPatch, state_dir: str
) -> None:
    client = _client(monkeypatch, state_dir)
    big = b"x" * (1024 * 1024 + 1)
    resp = client.post("/baselithbot/inbound/whatsapp", content=big)
    assert resp.status_code == 413


@pytest.mark.asyncio
async def test_read_body_capped_stops_streamed_body_without_length() -> None:
    from fastapi import HTTPException
    from starlette.requests import Request

    from plugins.baselithbot.inbound import read_body_capped

    sent = 0

    async def receive() -> dict[str, object]:
        nonlocal sent
        sent += 1
        # Endless chunked stream with no Content-Length header.
        return {"type": "http.request", "body": b"y" * 1024, "more_body": True}

    request = Request({"type": "http", "method": "POST", "headers": []}, receive)
    with pytest.raises(HTTPException) as exc:
        await read_body_capped(request, 4096)
    assert exc.value.status_code == 413
    assert sent <= 5  # stopped at the cap, not after buffering everything


# ---------------------------------------------------------------- signatures


def test_discord_signature_outside_replay_window_is_rejected(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pytest.importorskip("cryptography")
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

    from plugins.baselithbot.inbound import InboundAuthError, verify_inbound_request

    key = Ed25519PrivateKey.generate()
    public_hex = key.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw).hex()
    monkeypatch.setenv("DISCORD_PUBLIC_KEY", public_hex)
    body = b'{"type": 1}'

    def headers(ts: int) -> dict[str, str]:
        sig = key.sign(str(ts).encode() + body).hex()
        return {"X-Signature-Ed25519": sig, "X-Signature-Timestamp": str(ts)}

    verify_inbound_request(
        "discord", headers(int(time.time())), body
    )  # fresh: accepted
    with pytest.raises(InboundAuthError) as exc:
        verify_inbound_request("discord", headers(int(time.time()) - 3600), body)
    assert exc.value.status_code == 401


def test_non_ascii_signature_is_a_clean_rejection() -> None:
    from plugins.baselithbot.inbound import (
        verify_github_signature,
        verify_hmac_signature,
        verify_slack_signature,
        verify_telegram_secret_token,
    )

    assert verify_slack_signature("s", "1", b"x", "v0=é") is False
    assert verify_github_signature("s", b"x", "sha256=é") is False
    assert verify_telegram_secret_token("s", "é") is False
    assert verify_hmac_signature("s", b"x", "é") is False


def test_dashboard_non_ascii_bearer_is_403_not_500(
    monkeypatch: pytest.MonkeyPatch, state_dir: str
) -> None:
    client = _client(monkeypatch, state_dir)
    resp = client.get(
        "/baselithbot/status",
        headers={"Authorization": "Bearer caf\u00e9".encode("latin-1")},
    )
    assert resp.status_code == 403


def test_dashboard_token_not_in_repr() -> None:
    from plugins.baselithbot.policies import DashboardAuth

    auth = DashboardAuth(token="super-secret-dashboard-token")
    assert "super-secret-dashboard-token" not in repr(vars(auth))


# ---------------------------------------------------------------- replay tenancy


def test_replay_run_id_cannot_be_hijacked_across_tenants(tmp_path: Path) -> None:
    from plugins.baselithbot.control.replay import RunIdConflictError, TaskReplayStore

    store = TaskReplayStore(tmp_path / "replay.sqlite")
    store.start_run(run_id="r1", goal="a", start_url=None, max_steps=1, tenant_id="A")
    store.add_step(
        run_id="r1",
        step_index=1,
        action="type",
        reasoning="",
        current_url="https://bank.example",
        screenshot_b64="c2VjcmV0",
        extracted_data={},
    )
    with pytest.raises(RunIdConflictError):
        store.start_run(
            run_id="r1", goal="b", start_url=None, max_steps=1, tenant_id="B"
        )
    assert store.get_run("r1", tenant_id="B") is None
    assert store.get_run_step_screenshot("r1", 1, tenant_id="B") is None
    run = store.get_run("r1", tenant_id="A")
    assert run is not None and run["step_count"] == 1
    store.close()


def test_replay_same_tenant_reuse_drops_stale_steps(tmp_path: Path) -> None:
    from plugins.baselithbot.control.replay import TaskReplayStore

    store = TaskReplayStore(tmp_path / "replay.sqlite")
    store.start_run(run_id="r1", goal="a", start_url=None, max_steps=1)
    store.add_step(
        run_id="r1",
        step_index=1,
        action="x",
        reasoning="",
        current_url="",
        screenshot_b64=None,
        extracted_data={},
    )
    store.start_run(run_id="r1", goal="again", start_url=None, max_steps=1)
    run = store.get_run("r1")
    assert run is not None and run["step_count"] == 0
    store.close()


def test_run_rejects_malformed_client_run_id(
    monkeypatch: pytest.MonkeyPatch, state_dir: str
) -> None:
    client = _client(monkeypatch, state_dir)
    resp = client.post(
        "/baselithbot/run",
        json={"goal": "g", "run_id": "x" * 65},
        headers={"Authorization": f"Bearer {_TOKEN}"},
    )
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_replay_async_reads_run_off_loop(tmp_path: Path) -> None:
    from plugins.baselithbot.control.replay import TaskReplayStore

    store = TaskReplayStore(tmp_path / "replay.sqlite")
    await store.astart_run(run_id="r1", goal="a", start_url=None, max_steps=1)
    assert [r["run_id"] for r in await store.alist_runs()] == ["r1"]
    assert (await store.aget_run("r1", include_screenshots=False)) is not None
    assert await store.aprune_older_than(retention_seconds=-1.0) == 1
    store.close()

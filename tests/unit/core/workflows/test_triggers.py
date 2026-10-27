"""Inbound trigger authentication."""

import time

import pytest
from pydantic import SecretStr

from baselith.core.webhooks.signing import build_signature_header
from baselith.core.workflows.triggers import (
    TriggerAuthError,
    TriggerVerifier,
    derive_workflow_secret,
)

pytestmark = [pytest.mark.unit]

MASTER = "master-secret"
BODY = b'{"ticket": 7}'


def _sign(workflow_id: str, body: bytes = BODY, *, ts: int | None = None) -> str:
    return build_signature_header(
        derive_workflow_secret(MASTER, workflow_id), body, timestamp=ts
    )


def _verifier(tolerance: int = 300) -> TriggerVerifier:
    return TriggerVerifier(SecretStr(MASTER), tolerance)


def test_valid_signature_passes():
    _verifier().verify("w1", BODY, _sign("w1"))


def test_secret_is_per_workflow():
    assert derive_workflow_secret(MASTER, "a") != derive_workflow_secret(MASTER, "b")
    assert derive_workflow_secret(MASTER, "a") != derive_workflow_secret("other", "a")
    assert derive_workflow_secret(MASTER, "a") != MASTER


def test_signature_for_one_workflow_is_refused_on_another():
    with pytest.raises(TriggerAuthError, match="invalid"):
        _verifier().verify("w2", BODY, _sign("w1"))


def test_tampered_body_is_refused():
    with pytest.raises(TriggerAuthError):
        _verifier().verify("w1", BODY + b" ", _sign("w1"))


def test_stale_timestamp_is_refused():
    with pytest.raises(TriggerAuthError, match="expired"):
        _verifier(tolerance=60).verify(
            "w1", BODY, _sign("w1", ts=int(time.time()) - 600)
        )


def test_replay_is_refused_with_a_flag():
    verifier = _verifier()
    header = _sign("w1")
    verifier.verify("w1", BODY, header)
    with pytest.raises(TriggerAuthError) as exc:
        verifier.verify("w1", BODY, header)
    assert exc.value.replay is True


def test_same_body_signed_again_is_a_new_request():
    verifier = _verifier()
    now = int(time.time())
    verifier.verify("w1", BODY, _sign("w1", ts=now))
    verifier.verify("w1", BODY, _sign("w1", ts=now + 1))


@pytest.mark.parametrize("header", [None, "", "garbage", "t=abc,v1=ff", "v1=ff"])
def test_missing_or_malformed_header_is_refused(header):
    with pytest.raises(TriggerAuthError):
        _verifier().verify("w1", BODY, header)


@pytest.mark.parametrize("secret", [None, SecretStr("")])
def test_without_a_secret_nothing_is_served(secret):
    verifier = TriggerVerifier(secret, 300)
    assert verifier.configured is False
    with pytest.raises(TriggerAuthError, match="not configured"):
        verifier.verify("w1", BODY, _sign("w1"))


def test_non_ascii_signature_is_a_clean_refusal_not_a_crash():
    with pytest.raises(TriggerAuthError):
        _verifier().verify("w1", BODY, f"t={int(time.time())},v1=é")


def test_replay_memory_forgets_entries_older_than_the_window():
    verifier = _verifier(tolerance=60)
    verifier.verify("w1", BODY, _sign("w1"))
    assert len(verifier._seen) == 1
    verifier._evict(time.time() + 120)
    assert not verifier._seen


def test_replay_memory_is_bounded(monkeypatch):
    monkeypatch.setattr("baselith.core.workflows.triggers._MAX_REMEMBERED", 3)
    verifier = _verifier()
    now = int(time.time())
    for offset in range(6):
        verifier.verify("w1", BODY, _sign("w1", ts=now + offset))
    assert len(verifier._seen) <= 3

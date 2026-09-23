"""``WEBHOOK_STORE`` selects where subscriptions live.

Regression: the only store was in-memory, so endpoints vanished on restart and
the RQ worker — which emits ``agent.*`` events — never saw them.
"""

from __future__ import annotations

import pytest

import core.config.webhooks as webhook_config
import core.webhooks.service as webhook_service
from core.config.webhooks import WebhookConfig
from core.webhooks.store import InMemoryWebhookStore
from core.webhooks.store_postgres import PostgresWebhookStore


@pytest.fixture
def fresh_service(monkeypatch):
    monkeypatch.setattr(webhook_service, "_webhook_service", None)
    yield
    monkeypatch.setattr(webhook_service, "_webhook_service", None)


@pytest.mark.parametrize(
    ("store", "expected"),
    [("memory", InMemoryWebhookStore), ("postgres", PostgresWebhookStore)],
)
def test_setting_picks_the_store(fresh_service, monkeypatch, store, expected):
    monkeypatch.setattr(
        webhook_config, "_webhook_config", WebhookConfig(WEBHOOK_STORE=store)
    )
    assert isinstance(webhook_service.get_webhook_service().store, expected)


def test_default_stays_in_memory(monkeypatch):
    monkeypatch.delenv("WEBHOOK_STORE", raising=False)
    assert WebhookConfig().store == "memory"


def test_ciphertext_without_keys_fails_loudly(monkeypatch):
    # Returning the token as the secret would sign every delivery wrongly.
    from core.security.encryption import FieldEncryptor

    token = FieldEncryptor.from_keys({"k1": "a" * 32}).encrypt_bytes(
        b"whsec", aad=b"webhook:whe_1"
    )
    monkeypatch.setattr("core.security.get_field_encryptor", lambda: None)
    with pytest.raises(RuntimeError, match="DATA_ENCRYPTION_KEYS"):
        PostgresWebhookStore._open("whe_1", token)


def test_plaintext_rows_still_read_once_keys_are_configured(monkeypatch):
    from core.security.encryption import FieldEncryptor

    encryptor = FieldEncryptor.from_keys({"k1": "a" * 32})
    monkeypatch.setattr("core.security.get_field_encryptor", lambda: encryptor)
    assert PostgresWebhookStore._open("whe_1", "legacy-plain") == "legacy-plain"

"""Credential resolution and the UI-safe status snapshot."""

from __future__ import annotations

import pytest
from pydantic import SecretStr

from core.connectors import (
    ConnectorSpec,
    CredentialField,
    SecretsCredentialResolver,
    StaticCredentialResolver,
    credential_status,
    get_credential_resolver,
    set_credential_resolver,
)
from core.connectors.credentials import secret_names

SPEC = ConnectorSpec(
    name="acme_crm",
    display_name="Acme CRM",
    credentials=(
        CredentialField("api_key"),
        CredentialField("region", secret=False, required=False),
    ),
)


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    for key in (
        "CONNECTOR_ACME_CRM__API_KEY",
        "CONNECTOR_ACME_CRM__REGION",
        "CONNECTOR_ACME_CRM__ACME__API_KEY",
    ):
        monkeypatch.delenv(key, raising=False)
    yield
    set_credential_resolver(None)


async def test_secrets_resolver_reads_the_process_wide_name(monkeypatch):
    monkeypatch.setenv("CONNECTOR_ACME_CRM__API_KEY", "global-key")
    monkeypatch.setenv("CONNECTOR_ACME_CRM__REGION", "eu")
    creds = await SecretsCredentialResolver().resolve(SPEC, tenant_id="default")
    assert creds["api_key"].get_secret_value() == "global-key"
    assert creds["region"].get_secret_value() == "eu"


async def test_tenant_override_wins(monkeypatch):
    monkeypatch.setenv("CONNECTOR_ACME_CRM__API_KEY", "global-key")
    monkeypatch.setenv("CONNECTOR_ACME_CRM__ACME__API_KEY", "tenant-key")
    resolver = SecretsCredentialResolver()
    tenant = await resolver.resolve(SPEC, tenant_id="acme")
    other = await resolver.resolve(SPEC, tenant_id="globex")
    assert tenant["api_key"].get_secret_value() == "tenant-key"
    assert other["api_key"].get_secret_value() == "global-key"


async def test_unset_fields_are_absent():
    creds = await SecretsCredentialResolver().resolve(SPEC, tenant_id="default")
    assert creds == {}


async def test_static_resolver_filters_to_declared_fields():
    resolver = StaticCredentialResolver(
        {"acme_crm": {"api_key": "k", "unrelated": "x"}}
    )
    creds = await resolver.resolve(SPEC, tenant_id="default")
    assert set(creds) == {"api_key"}
    assert isinstance(creds["api_key"], SecretStr)


def test_default_resolver_is_the_secrets_resolver():
    assert isinstance(get_credential_resolver(), SecretsCredentialResolver)
    custom = StaticCredentialResolver({})
    set_credential_resolver(custom)
    assert get_credential_resolver() is custom


def test_credential_status_never_contains_values():
    status = credential_status(SPEC, {"api_key": SecretStr("s3cr3t")})
    assert status == {
        "configured": True,
        "missing": [],
        "fields": {
            "api_key": {"set": True, "secret": True, "required": True},
            "region": {"set": False, "secret": False, "required": False},
        },
    }
    assert "s3cr3t" not in repr(status)


def test_credential_status_reports_missing_required_fields():
    status = credential_status(SPEC, {"api_key": SecretStr("")})
    assert status["configured"] is False
    assert status["missing"] == ["api_key"]


def test_secret_names_are_injective_across_tenants():
    names = {
        secret_names(SPEC, "api_key", tenant)[0]
        for tenant in ("tenant-a", "tenant_a", "Tenant-A", "tenant.a", "tenanta")
    }
    assert len(names) == 5


def test_plain_slug_tenants_stay_readable():
    assert secret_names(SPEC, "api_key", "acme") == [
        "CONNECTOR_ACME_CRM__ACME__API_KEY",
        "CONNECTOR_ACME_CRM__API_KEY",
    ]
    assert (
        secret_names(SPEC, "api_key", "tenant-a")[0]
        == "CONNECTOR_ACME_CRM__TENANT_2D_A__API_KEY"
    )


async def test_escaped_tenant_override_resolves(monkeypatch):
    monkeypatch.setenv("CONNECTOR_ACME_CRM__TENANT_2D_A__API_KEY", "tenant-key")
    creds = await SecretsCredentialResolver().resolve(SPEC, tenant_id="tenant-a")
    assert creds["api_key"].get_secret_value() == "tenant-key"


@pytest.mark.parametrize("name", ["crm__acme", "crm_", "a__b"])
def test_connector_names_cannot_contain_the_separator(name):
    with pytest.raises(ValueError):
        ConnectorSpec(name=name, display_name="x")


@pytest.mark.parametrize("field", ["Api", "api__key", "_key", "key_", "api-key", ""])
def test_credential_field_names_are_identifiers(field):
    with pytest.raises(ValueError):
        CredentialField(field)

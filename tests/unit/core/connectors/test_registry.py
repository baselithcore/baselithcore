"""ConnectorRegistry and create_connector."""

from __future__ import annotations

import pytest

from core.connectors import (
    BaseConnector,
    ConnectorCapability,
    ConnectorConfigError,
    ConnectorEntry,
    ConnectorRegistry,
    ConnectorSpec,
    CredentialField,
    StaticCredentialResolver,
    create_connector,
    get_connector_registry,
    reset_connector_registry,
)
from core.context import reset_tenant_context, set_tenant_context
from core.exceptions import DuplicateRegistrationError


class Crm(BaseConnector):
    spec = ConnectorSpec(
        name="crm",
        display_name="CRM",
        capabilities=frozenset({ConnectorCapability.LOOKUP}),
        credentials=(CredentialField("api_key"),),
    )

    async def lookup(self, key):
        return {"key": key}


class Mailer(BaseConnector):
    spec = ConnectorSpec(name="mailer", display_name="Mailer")


@pytest.fixture(autouse=True)
def _fresh_registry():
    reset_connector_registry()
    yield
    reset_connector_registry()


def test_register_a_connector_class_and_read_it_back():
    registry = ConnectorRegistry()
    entry = registry.register_connector(Crm, owner="crm_plugin")
    assert entry.spec is Crm.spec
    assert entry.owner == "crm_plugin"
    assert registry.require("crm") is entry
    assert registry.specs() == [Crm.spec]


def test_register_an_explicit_entry():
    registry = ConnectorRegistry()
    registry.register_connector(ConnectorEntry(spec=Mailer.spec, factory=Mailer))
    assert "mailer" in registry


def test_register_rejects_objects_without_a_spec():
    with pytest.raises(TypeError):
        ConnectorRegistry().register_connector(object())


def test_duplicate_name_is_refused_by_default():
    registry = ConnectorRegistry()
    registry.register_connector(Crm, owner="a")
    with pytest.raises(DuplicateRegistrationError):
        registry.register_connector(Crm, owner="b")


def test_remove_owned_by_only_removes_that_owner():
    registry = ConnectorRegistry()
    registry.register_connector(Crm, owner="a")
    registry.register_connector(Mailer, owner="b")
    assert registry.remove_owned_by("a") == ["crm"]
    assert registry.names() == ["mailer"]


def test_with_capability_filters():
    registry = ConnectorRegistry()
    registry.register_connector(Crm)
    registry.register_connector(Mailer)
    names = [e.spec.name for e in registry.with_capability(ConnectorCapability.LOOKUP)]
    assert names == ["crm"]


def test_global_registry_is_a_singleton():
    assert get_connector_registry() is get_connector_registry()


async def test_create_connector_resolves_credentials_for_the_bound_tenant():
    seen: list[str] = []

    class Recording(StaticCredentialResolver):
        async def resolve(self, spec, *, tenant_id):
            seen.append(tenant_id)
            return await super().resolve(spec, tenant_id=tenant_id)

    get_connector_registry().register_connector(Crm)
    token = set_tenant_context("tenant-a")
    try:
        connector = await create_connector(
            "crm", resolver=Recording({"crm": {"api_key": "k"}})
        )
    finally:
        reset_tenant_context(token)
    assert isinstance(connector, Crm)
    assert connector.credential("api_key") == "k"
    assert seen == ["tenant-a"]


async def test_create_connector_explicit_tenant_and_registry():
    registry = ConnectorRegistry()
    registry.register_connector(Crm)
    connector = await create_connector(
        "crm",
        tenant_id="t1",
        registry=registry,
        resolver=StaticCredentialResolver({"crm": {"api_key": "k"}}),
    )
    assert await connector.lookup("x") == {"key": "x"}


async def test_create_unknown_connector_is_a_config_error():
    with pytest.raises(ConnectorConfigError, match="nope"):
        await create_connector("nope", resolver=StaticCredentialResolver({}))


def test_open_egress_spec_is_warned_about_in_production(monkeypatch, caplog):
    import logging

    from core.connectors import registry as registry_module

    monkeypatch.setattr(registry_module, "is_production_env", lambda: True)
    with caplog.at_level(logging.WARNING):
        get_connector_registry().register_connector(Mailer)
    assert "allowed_hosts" in caplog.text and "mailer" in caplog.text


def test_open_egress_spec_is_silent_outside_production(monkeypatch, caplog):
    import logging

    from core.connectors import registry as registry_module

    monkeypatch.setattr(registry_module, "is_production_env", lambda: False)
    with caplog.at_level(logging.WARNING):
        get_connector_registry().register_connector(Mailer)
    assert "allowed_hosts" not in caplog.text

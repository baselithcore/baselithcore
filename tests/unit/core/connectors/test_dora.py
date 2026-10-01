"""Declaring connector vendors into the DORA register of information."""

from __future__ import annotations

from core.connectors import (
    ConnectorEntry,
    ConnectorRegistry,
    ConnectorSpec,
    declare_ict_providers,
    vendor_provider_id,
)
from core.thirdparty import RegisterOfInformation


def _registry(*specs: ConnectorSpec) -> ConnectorRegistry:
    registry = ConnectorRegistry()
    for spec in specs:
        registry.register_connector(ConnectorEntry(spec=spec, factory=lambda **_: None))
    return registry


async def test_vendors_become_providers_with_stable_ids():
    registry = _registry(
        ConnectorSpec(
            name="sf_crm",
            display_name="CRM",
            vendor="Salesforce, Inc.",
            vendor_country="US",
        ),
        ConnectorSpec(name="sf_mkt", display_name="Mkt", vendor="Salesforce, Inc."),
        ConnectorSpec(name="local", display_name="No vendor"),
    )
    register = RegisterOfInformation()
    ids = await declare_ict_providers(registry=registry, register=register)
    providers = await register.list_providers()
    assert len(providers) == 1
    assert ids == [vendor_provider_id("Salesforce, Inc.")]
    assert providers[0].name == "Salesforce, Inc."
    assert providers[0].country == "US"


async def test_declaring_twice_keeps_operator_edits():
    registry = _registry(
        ConnectorSpec(
            name="acme", display_name="Acme", vendor="Acme S.p.A.", vendor_lei="LEI123"
        )
    )
    register = RegisterOfInformation()
    [pid] = await declare_ict_providers(registry=registry, register=register)
    provider = await register.get_provider(pid)
    assert provider is not None and provider.lei == "LEI123"
    provider.total_annual_expense = 12000.0
    await register.register_provider(provider)
    await declare_ict_providers(registry=registry, register=register)
    again = await register.get_provider(pid)
    assert again is not None and again.total_annual_expense == 12000.0
    assert len(await register.list_providers()) == 1

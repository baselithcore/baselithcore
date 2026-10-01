"""Spec validation and value-type behaviour."""

from __future__ import annotations

import pytest

from core.connectors import (
    ActionSpec,
    ConnectorCapability,
    ConnectorHealth,
    ConnectorSpec,
    HealthState,
    SyncPage,
)


@pytest.mark.parametrize("name", ["Acme", "1acme", "acme-crm", "", "a" * 65])
def test_spec_rejects_bad_names(name):
    with pytest.raises(ValueError):
        ConnectorSpec(name=name, display_name="x")


def test_spec_rejects_actions_without_the_action_capability():
    with pytest.raises(ValueError, match="action"):
        ConnectorSpec(
            name="acme",
            display_name="Acme",
            actions=(ActionSpec("create", "Create"),),
        )


def test_spec_rejects_duplicate_actions():
    with pytest.raises(ValueError, match="duplicate"):
        ConnectorSpec(
            name="acme",
            display_name="Acme",
            capabilities=frozenset({ConnectorCapability.ACTION}),
            actions=(ActionSpec("a", "x"), ActionSpec("a", "y")),
        )


def test_spec_rejects_zero_attempts():
    with pytest.raises(ValueError):
        ConnectorSpec(name="acme", display_name="Acme", max_attempts=0)


def test_action_defaults_to_the_restrictive_category():
    assert ActionSpec("a", "x").category == "destructive"


def test_action_rejects_unknown_category():
    with pytest.raises(ValueError, match="category"):
        ActionSpec("a", "x", category="whatever")


def test_spec_action_lookup():
    create = ActionSpec("create", "Create", category="mutating")
    spec = ConnectorSpec(
        name="acme",
        display_name="Acme",
        capabilities=frozenset({ConnectorCapability.ACTION}),
        actions=(create,),
    )
    assert spec.action("create") is create
    assert spec.action("missing") is None


def test_sync_page_has_more_follows_the_cursor():
    assert SyncPage(items=[], next_cursor="c2").has_more
    assert not SyncPage(items=[]).has_more


def test_health_ok_only_for_ok_state():
    assert ConnectorHealth(HealthState.OK).ok
    assert not ConnectorHealth(HealthState.DEGRADED).ok

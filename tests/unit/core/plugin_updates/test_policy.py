"""The update policy document: channels, the maintenance window, proposals."""

from __future__ import annotations

from datetime import UTC, datetime, time, timedelta

import pytest
from pydantic import ValidationError

from baselith.core.plugin_updates.policy import (
    POLICY_OFF,
    MaintenanceWindow,
    PolicyProposal,
    UpdatePolicy,
)


def _window(**over: object) -> MaintenanceWindow:
    fields: dict[str, object] = {
        "days": frozenset({0, 1, 2, 3, 4}),
        "start": time(2, 0),
        "end": time(4, 0),
        "tz": "Europe/Rome",
    }
    return MaintenanceWindow.model_validate(fields | over)


def test_policy_off_by_default() -> None:
    assert POLICY_OFF == UpdatePolicy()
    assert POLICY_OFF.core == "off" and POLICY_OFF.plugins == "off"
    assert POLICY_OFF.window is None and POLICY_OFF.require_signed is True
    assert not POLICY_OFF.enabled


def test_channel_on_requires_a_window() -> None:
    with pytest.raises(ValidationError, match="maintenance window"):
        UpdatePolicy(plugins="patch")
    with pytest.raises(ValidationError, match="maintenance window"):
        UpdatePolicy(core="patch")
    policy = UpdatePolicy(plugins="patch", window=_window())
    assert policy.enabled


def test_unknown_timezone_rejected() -> None:
    with pytest.raises(ValidationError, match="unknown timezone"):
        _window(tz="Mars/Olympus")
    with pytest.raises(ValidationError, match="unknown timezone"):
        _window(tz="../../etc/passwd")
    with pytest.raises(ValidationError, match="unknown timezone"):
        _window(tz="")


def test_days_must_be_0_to_6() -> None:
    with pytest.raises(ValidationError):
        _window(days=frozenset({7}))
    with pytest.raises(ValidationError):
        _window(days=frozenset({-1}))
    with pytest.raises(ValidationError):
        _window(days=frozenset())


def test_start_equal_end_rejected() -> None:
    with pytest.raises(ValidationError, match="start and end"):
        _window(start=time(3, 0), end=time(3, 0))


def test_window_times_are_wall_clock() -> None:
    with pytest.raises(ValidationError, match="wall-clock"):
        _window(start=time(2, 0, tzinfo=UTC))


def test_require_signed_cannot_be_turned_off() -> None:
    with pytest.raises(ValidationError):
        UpdatePolicy.model_validate({"require_signed": False})


def test_unknown_fields_are_refused() -> None:
    with pytest.raises(ValidationError):
        UpdatePolicy.model_validate({"core": "off", "beta": True})


def test_allowlist_names_are_plugin_names() -> None:
    policy = UpdatePolicy(plugins="patch", window=_window(), plugin_allowlist={"aura"})
    assert policy.plugin_allowlist == frozenset({"aura"})
    with pytest.raises(ValidationError, match="plugin name"):
        UpdatePolicy(plugin_allowlist={"../etc"})


def test_policy_roundtrips_through_json() -> None:
    policy = UpdatePolicy(
        core="patch", plugins="patch", window=_window(), plugin_allowlist={"a", "b"}
    )
    assert UpdatePolicy.model_validate_json(policy.model_dump_json()) == policy


def test_proposal_digest_names_the_policy() -> None:
    at = datetime(2026, 11, 14, 10, 0, tzinfo=UTC)
    one = PolicyProposal(
        policy=UpdatePolicy(plugins="patch", window=_window()),
        proposed_by="alice",
        proposed_at=at,
        expires_at=at + timedelta(hours=1),
    )
    two = one.model_copy(update={"policy": UpdatePolicy()})
    assert one.digest != two.digest
    assert len(one.digest) == 64

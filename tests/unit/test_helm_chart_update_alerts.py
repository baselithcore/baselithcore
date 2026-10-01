"""Chart tests for the update-notice alert group and the update cache dir."""

from __future__ import annotations

import yaml

from tests.unit.helm import documents, render


def _groups(*args: str) -> list[dict]:
    rendered = render("--set", "prometheusRule.enabled=true", *args)
    objects = [
        doc
        for doc in yaml.safe_load_all(rendered)
        if doc and doc["kind"] == "PrometheusRule"
    ]
    assert len(objects) == 1, [doc["metadata"]["name"] for doc in objects]
    return objects[0]["spec"]["groups"]


def test_update_notices_live_in_their_own_group() -> None:
    """Notices about a release are not health: a separate group keeps them off
    the health alerts' evaluation instant and droppable as a unit."""
    groups = _groups()
    assert len(groups) == 2
    rules = {rule["alert"]: rule for rule in groups[1]["rules"]}
    assert set(rules) == {
        "BaselithcoreUpdateAvailable",
        "BaselithcoreSecurityUpdateAvailable",
    }
    plain = rules["BaselithcoreUpdateAvailable"]
    security = rules["BaselithcoreSecurityUpdateAvailable"]
    assert 'security="false"' in plain["expr"] and "== 1" in plain["expr"]
    assert 'security="true"' in security["expr"] and "== 1" in security["expr"]
    assert (plain["for"], plain["labels"]["severity"]) == ("1h", "info")
    assert (security["for"], security["labels"]["severity"]) == ("5m", "warning")
    for rule in rules.values():
        assert "baselith_update_available" in rule["expr"]
        assert "by (component)" in rule["expr"]
        assert rule["annotations"]["summary"]


def test_update_group_disappears_when_both_are_disabled() -> None:
    groups = _groups(
        "--set",
        "prometheusRule.disabledAlerts[0]=BaselithcoreUpdateAvailable",
        "--set",
        "prometheusRule.disabledAlerts[1]=BaselithcoreSecurityUpdateAvailable",
    )
    assert len(groups) == 1


def test_update_cache_dir_is_writable_under_a_read_only_root() -> None:
    """The checker's default cache path lies in the read-only image tree."""
    config = next(
        doc
        for doc in documents(render())
        if doc["kind"] == "ConfigMap" and doc["metadata"]["name"].endswith("-config")
    )
    assert config["data"]["PLUGIN_UPDATE_CACHE_DIR"].startswith("/tmp/")

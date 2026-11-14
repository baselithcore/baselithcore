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
    every = {rule["alert"]: rule for rule in groups[1]["rules"]}
    assert set(every) == {
        "BaselithcoreUpdateAvailable",
        "BaselithcoreSecurityUpdateAvailable",
        "BaselithcoreUpdateRollbackFailed",
        "BaselithcoreUpdateRunStuck",
    }
    rules = {
        name: rule
        for name, rule in every.items()
        if "baselith_update_available" in rule["expr"]
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


def test_update_run_alerts_render_and_the_group_disappears_when_all_are_disabled() -> (
    None
):
    rules = {rule["alert"]: rule for rule in _groups()[1]["rules"]}
    failed = rules["BaselithcoreUpdateRollbackFailed"]
    stuck = rules["BaselithcoreUpdateRunStuck"]
    assert "baselith_update_rollback_failed_runs{" in failed["expr"]
    assert (failed["for"], failed["labels"]["severity"]) == ("5m", "critical")
    assert "baselith_update_run_inflight_age_seconds{" in stuck["expr"]
    assert (stuck["for"], stuck["labels"]["severity"]) == ("10m", "warning")
    names = (
        "BaselithcoreUpdateAvailable",
        "BaselithcoreSecurityUpdateAvailable",
        "BaselithcoreUpdateRollbackFailed",
        "BaselithcoreUpdateRunStuck",
    )
    args: list[str] = []
    for i, name in enumerate(names):
        args += ["--set", f"prometheusRule.disabledAlerts[{i}]={name}"]
    assert len(_groups(*args)) == 1
    assert len(_groups(*args[:6])) == 2


def test_update_cache_dir_is_writable_under_a_read_only_root() -> None:
    """The checker's default cache path lies in the read-only image tree."""
    config = next(
        doc
        for doc in documents(render())
        if doc["kind"] == "ConfigMap" and doc["metadata"]["name"].endswith("-config")
    )
    assert config["data"]["PLUGIN_UPDATE_CACHE_DIR"].startswith("/tmp/")

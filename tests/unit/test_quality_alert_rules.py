"""Alert rules on the online-evaluation quality monitor (compose and Helm).

``promtool`` is not part of the toolchain, so these check the structure a rule
loader needs and the one thing that silently breaks an alert: an expression
naming a metric, or a label value, that the code does not export.
"""

from __future__ import annotations

from pathlib import Path

import yaml

from baselith.core.observability.metrics import (
    EVAL_ONLINE_DEGRADED,
    EVAL_ONLINE_FAILURES_TOTAL,
)
from tests.unit.helm import render

ROOT = Path(__file__).resolve().parents[2]
RULES = ROOT / "deploy" / "prometheus" / "alert-rules.yml"
QUALITY_ALERTS = {"AnswerQualityDegraded", "OnlineEvalMonitorFailing"}


def _compose_rules() -> dict[str, dict]:
    doc = yaml.safe_load(RULES.read_text(encoding="utf-8"))
    rules: dict[str, dict] = {}
    for group in doc["groups"]:
        assert group["name"] and group["rules"], group
        for rule in group["rules"]:
            rules[rule["alert"]] = rule
    return rules


def _balanced(expr: str) -> bool:
    depth = 0
    for char in expr:
        depth += {"(": 1, ")": -1, "{": 1, "}": -1}.get(char, 0)
        if depth < 0:
            return False
    return depth == 0


def test_compose_rules_are_loadable_and_actionable() -> None:
    rules = _compose_rules()
    assert QUALITY_ALERTS <= set(rules)
    for name, rule in rules.items():
        assert set(rule) >= {"alert", "expr", "for", "labels", "annotations"}, name
        assert rule["labels"]["severity"] in {"critical", "warning", "info"}, name
        assert rule["annotations"]["summary"], name
        assert rule["annotations"]["description"], name
        assert _balanced(rule["expr"]), name


def test_compose_prometheus_loads_the_rules_file() -> None:
    config = yaml.safe_load((ROOT / "prometheus.yml").read_text(encoding="utf-8"))
    assert "/etc/prometheus/alert-rules.yml" in config["rule_files"]


def test_quality_rules_name_the_exported_metrics() -> None:
    rules = _compose_rules()
    degraded = rules["AnswerQualityDegraded"]["expr"]
    assert EVAL_ONLINE_DEGRADED._name in degraded
    assert "== 1" in degraded
    assert "by (intent, source)" in degraded
    failing = rules["OnlineEvalMonitorFailing"]["expr"]
    assert f"{EVAL_ONLINE_FAILURES_TOTAL._name}_total" in failing


def test_failure_stages_in_the_rules_are_ones_the_code_records() -> None:
    sources = "".join(
        (ROOT / "baselith" / "core" / "evaluation" / name).read_text(encoding="utf-8")
        for name in ("online_fleet.py", "service.py")
    )
    for stage in ("fleet", "monitor"):
        assert f'record_failure("{stage}")' in sources, stage


def _helm_health_rules(*args: str) -> dict[str, dict]:
    rendered = render("--set", "prometheusRule.enabled=true", *args)
    (rule_object,) = [
        doc
        for doc in yaml.safe_load_all(rendered)
        if doc and doc["kind"] == "PrometheusRule"
    ]
    return {rule["alert"]: rule for rule in rule_object["spec"]["groups"][0]["rules"]}


def test_helm_chart_ships_the_quality_alerts() -> None:
    rules = _helm_health_rules()
    degraded = rules["BaselithcoreAnswerQualityDegraded"]
    expr = " ".join(degraded["expr"].split())
    assert "max by (intent, source) (mas_eval_online_degraded{" in expr
    assert 'namespace="default"' in expr and expr.endswith("== 1")
    assert degraded["labels"] == {"severity": "warning", "component": "evaluation"}
    # The label templates survive Helm and reach Prometheus intact.
    assert "{{ $labels.intent }}" in degraded["annotations"]["summary"]
    failing = rules["BaselithcoreOnlineEvalMonitorFailing"]
    assert 'stage=~"fleet|monitor"' in failing["expr"]
    for rule in (degraded, failing):
        assert _balanced(rule["expr"]), rule["alert"]


def test_helm_quality_alerts_can_be_disabled() -> None:
    rules = _helm_health_rules(
        "--set", "prometheusRule.disabledAlerts[0]=BaselithcoreAnswerQualityDegraded"
    )
    assert "BaselithcoreAnswerQualityDegraded" not in rules
    assert "BaselithcoreOnlineEvalMonitorFailing" in rules


def test_dashboard_charts_the_series_the_alert_pages_on() -> None:
    import json

    dashboard = json.loads(
        (ROOT / "grafana" / "dashboards" / "agentic-metrics.json").read_text(
            encoding="utf-8"
        )
    )
    panels = dashboard["panels"]
    ids = [panel["id"] for panel in panels]
    assert len(ids) == len(set(ids))
    exprs = [t["expr"] for panel in panels for t in panel.get("targets", [])]
    assert any(EVAL_ONLINE_DEGRADED._name in expr for expr in exprs)
    assert any("mas_eval_online_score_sum" in expr for expr in exprs)

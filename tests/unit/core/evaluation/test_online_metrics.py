"""Online evaluation metrics and label cardinality cap."""

from __future__ import annotations

from prometheus_client import REGISTRY

from baselith.core.evaluation.online_metrics import (
    OTHER_INTENT,
    IntentLabeler,
    record_drop,
    record_flags,
    record_score,
    set_degraded,
)


def _value(name: str, labels: dict[str, str]) -> float:
    return REGISTRY.get_sample_value(name, labels) or 0.0


def test_labeler_caps_distinct_intents() -> None:
    labeler = IntentLabeler(max_intents=2)
    assert labeler.label("a") == "a"
    assert labeler.label("b") == "b"
    assert labeler.label("c") == OTHER_INTENT
    assert labeler.label("a") == "a"  # admitted intents keep their label
    assert labeler.label("d") == OTHER_INTENT


def test_labeler_truncates_long_intents() -> None:
    assert len(IntentLabeler(5).label("x" * 500)) == 64


def test_score_and_quality_are_labelled_by_intent_and_source() -> None:
    labels = {"intent": "metrics_probe", "source": "checks"}
    before = _value("mas_eval_online_score_count", labels)
    quality_before = _value(
        "mas_eval_online_quality_total", labels | {"quality": "good"}
    )
    record_score("metrics_probe", "checks", 0.8, "good")
    assert _value("mas_eval_online_score_count", labels) == before + 1
    assert (
        _value("mas_eval_online_quality_total", labels | {"quality": "good"})
        == quality_before + 1
    )


def test_flags_drops_and_gauge() -> None:
    flag_labels = {"intent": "metrics_probe", "flag": "refusal"}
    before = _value("mas_eval_online_flags_total", flag_labels)
    record_flags("metrics_probe", ("refusal",))
    assert _value("mas_eval_online_flags_total", flag_labels) == before + 1

    drop_before = _value("mas_eval_online_dropped_total", {"reason": "queue_full"})
    record_drop("queue_full")
    assert (
        _value("mas_eval_online_dropped_total", {"reason": "queue_full"})
        == drop_before + 1
    )

    gauge = {"intent": "metrics_probe", "source": "judge"}
    set_degraded("metrics_probe", "judge", True)
    assert _value("mas_eval_online_degraded", gauge) == 1.0
    set_degraded("metrics_probe", "judge", False)
    assert _value("mas_eval_online_degraded", gauge) == 0.0

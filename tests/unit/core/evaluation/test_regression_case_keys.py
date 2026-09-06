"""The regression loader must accept every field the trajectory evaluator honours."""

from __future__ import annotations

from pathlib import Path

import yaml

from core.evaluation.regression_runner import ALLOWED_CASE_KEYS, load_cases
from core.evaluation.trajectory import TrajectoryCase


def test_loader_allowlist_matches_evaluator_schema() -> None:
    assert ALLOWED_CASE_KEYS == frozenset(TrajectoryCase.__annotations__)
    for field in ("expected_tool_order", "expected_tool_args", "reference_fact"):
        assert field in ALLOWED_CASE_KEYS


def test_order_args_and_reference_fields_load(tmp_path: Path) -> None:
    (tmp_path / "cases.yaml").write_text(
        yaml.safe_dump(
            [
                {
                    "case_id": "c1",
                    "input": "q",
                    "expected_tool_order": ["plan_task", "execute_code"],
                    "expected_tool_args": {
                        "scrape_url": {"url": "https://example.com"}
                    },
                    "reference_fact": "pgvector",
                }
            ]
        ),
        encoding="utf-8",
    )

    cases = load_cases(tmp_path)

    assert cases[0]["expected_tool_order"] == ["plan_task", "execute_code"]
    assert cases[0]["reference_fact"] == "pgvector"

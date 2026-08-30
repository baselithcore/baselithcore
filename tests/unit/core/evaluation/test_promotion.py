"""Unit tests for ``core.evaluation.promotion``.

Covers the scrub step (PII redaction + indirect-injection sanitizing), the
checkpoint -> recorded-run mapping (round-tripped through the regression
runner's own loader), and the ``promote_run`` end-to-end path including its
fail-closed refusals. No LLM, no DB — the in-memory checkpoint store backs
every test and all files land in ``tmp_path``.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml

from core.evaluation.promotion import (
    PromotionError,
    checkpoint_to_recorded_run,
    promote_run,
    scrub_text,
)
from core.evaluation.regression_runner import (
    load_cases,
    load_recorded_runs,
    run_regression,
)
from core.orchestration.checkpoint import (
    STATUS_COMPLETED,
    STATUS_RUNNING,
    Checkpoint,
    InMemoryCheckpointStore,
)

ZWSP = "\u200b"
RUN_ID = "run-abc123"


def _make_checkpoint(
    run_id: str = RUN_ID, status: str = STATUS_COMPLETED
) -> Checkpoint:
    """A realistic completed checkpoint with PII and a hidden character."""
    return Checkpoint(
        run_id=run_id,
        tenant_id="tenant-1",
        query=f"Where does alice@example.com{ZWSP} keep the Q3 report?",
        intent="rag",
        status=status,
        step=2,
        budget={"iterations": 2, "tool_calls": 2, "cost_usd": 0.034},
        trajectory=[
            {
                "cursor": 0,
                "tool": "search_knowledge_base",
                "args": {"query": "Q3 report location"},
                "category": "tool",
            },
            {
                "cursor": 1,
                "tool": "scrape_url",
                "args": {"url": "https://example.com"},
                "category": "tool",
            },
        ],
        steps={
            "0:search_knowledge_base:deadbeef": {
                "tool_name": "search_knowledge_base",
                "args": {"query": "Q3 report location"},
                "result": "Report owned by carol@example.com",
                "category": "tool",
                "at": 101.0,
            },
        },
        answer="Contact bob@example.com for the Q3 report.",
        created_at=100.0,
        updated_at=103.5,
    )


class TestScrubText:
    def test_redacts_email_and_reports_note(self) -> None:
        cleaned, notes = scrub_text("Please mail alice@example.com today.")
        assert "alice@example.com" not in cleaned
        assert "[EMAIL_REDACTED]" in cleaned
        assert "pii:email" in notes

    def test_strips_zero_width_and_reports_note(self) -> None:
        cleaned, notes = scrub_text(f"click{ZWSP} here")
        assert ZWSP not in cleaned
        assert cleaned == "click here"
        assert "indirect:zero_width" in notes

    def test_clean_text_passes_unchanged_with_no_notes(self) -> None:
        text = "BaselithCore supports Postgres, Qdrant, and Redis."
        assert scrub_text(text) == (text, [])

    def test_combined_pii_and_injection(self) -> None:
        cleaned, notes = scrub_text(f"ask bob@example.com{ZWSP}")
        assert "bob@example.com" not in cleaned
        assert ZWSP not in cleaned
        assert "pii:email" in notes
        assert "indirect:zero_width" in notes


class TestCheckpointToRecordedRun:
    def test_field_mapping(self) -> None:
        run = checkpoint_to_recorded_run(_make_checkpoint().to_dict())
        assert run["case_id"] == RUN_ID
        assert run["output_text"] == "Contact [EMAIL_REDACTED] for the Q3 report."
        assert run["latency_ms"] == 3500
        assert run["cost_usd"] == pytest.approx(0.034)
        names = [t["name"] for t in run["trajectory"]]
        assert names == ["search_knowledge_base", "scrape_url"]
        assert run["trajectory"][0]["args"] == {"query": "Q3 report location"}

    def test_scrubs_query_and_tool_observations(self) -> None:
        notes: list[str] = []
        run = checkpoint_to_recorded_run(_make_checkpoint().to_dict(), notes=notes)
        assert run["query"] == "Where does [EMAIL_REDACTED] keep the Q3 report?"
        assert run["trajectory"][0]["observation"] == "Report owned by [EMAIL_REDACTED]"
        assert "pii:email" in notes
        assert "indirect:zero_width" in notes

    def test_round_trips_through_regression_loader(self, tmp_path: Path) -> None:
        run = checkpoint_to_recorded_run(_make_checkpoint().to_dict())
        runs_file = tmp_path / "runs.json"
        runs_file.write_text(json.dumps([run]), encoding="utf-8")
        loaded = load_recorded_runs(runs_file)
        assert set(loaded) == {RUN_ID}
        recorded = loaded[RUN_ID]
        assert recorded.output_text == run["output_text"]
        assert recorded.latency_ms == 3500
        assert recorded.cost_usd == pytest.approx(0.034)
        assert [t.get("name") for t in recorded.trajectory] == [
            "search_knowledge_base",
            "scrape_url",
        ]

    def test_refuses_checkpoint_without_run_id(self) -> None:
        with pytest.raises(PromotionError):
            checkpoint_to_recorded_run({"query": "q", "answer": "a"})


class TestPromoteRun:
    async def test_promotes_completed_run_and_writes_case(self, tmp_path: Path) -> None:
        store = InMemoryCheckpointStore()
        await store.save(_make_checkpoint())
        runs_file = tmp_path / "runs" / "recorded_runs.json"
        cases_dir = tmp_path / "cases"

        result = await promote_run(
            store, RUN_ID, runs_file=runs_file, cases_dir=cases_dir
        )

        assert result.run_id == RUN_ID
        assert "pii:email" in result.scrubbed
        assert "indirect:zero_width" in result.scrubbed
        runs = json.loads(runs_file.read_text(encoding="utf-8"))
        assert [r["case_id"] for r in runs] == [RUN_ID]

        assert result.case_path is not None and result.case_path.exists()
        cases = load_cases(cases_dir)
        assert len(cases) == 1
        case = cases[0]
        assert case["case_id"] == RUN_ID
        assert case["input"] == "Where does [EMAIL_REDACTED] keep the Q3 report?"
        assert case["expected_tools"] == ["search_knowledge_base", "scrape_url"]
        assert case["max_tool_calls"] == 4  # len(trajectory) + 2

        # The promoted pair must pass the deterministic gate end-to-end.
        report = run_regression(cases, load_recorded_runs(runs_file))
        assert report.pass_rate == 1.0

    async def test_runs_only_when_cases_dir_omitted(self, tmp_path: Path) -> None:
        store = InMemoryCheckpointStore()
        await store.save(_make_checkpoint())
        runs_file = tmp_path / "recorded_runs.json"
        result = await promote_run(store, RUN_ID, runs_file=runs_file)
        assert result.case_path is None
        assert len(json.loads(runs_file.read_text(encoding="utf-8"))) == 1

    async def test_refuses_non_completed_run(self, tmp_path: Path) -> None:
        store = InMemoryCheckpointStore()
        await store.save(_make_checkpoint(status=STATUS_RUNNING))
        with pytest.raises(PromotionError, match="status"):
            await promote_run(store, RUN_ID, runs_file=tmp_path / "runs.json")
        assert not (tmp_path / "runs.json").exists()

    async def test_refuses_unknown_run(self, tmp_path: Path) -> None:
        store = InMemoryCheckpointStore()
        with pytest.raises(PromotionError, match="not found"):
            await promote_run(store, "missing", runs_file=tmp_path / "runs.json")

    async def test_refuses_duplicate_run_id(self, tmp_path: Path) -> None:
        store = InMemoryCheckpointStore()
        await store.save(_make_checkpoint())
        runs_file = tmp_path / "runs.json"
        await promote_run(store, RUN_ID, runs_file=runs_file)
        with pytest.raises(PromotionError, match="already"):
            await promote_run(store, RUN_ID, runs_file=runs_file)
        assert len(json.loads(runs_file.read_text(encoding="utf-8"))) == 1

    async def test_appends_to_existing_runs_file(self, tmp_path: Path) -> None:
        store = InMemoryCheckpointStore()
        await store.save(_make_checkpoint())
        await store.save(_make_checkpoint(run_id="run-def456"))
        runs_file = tmp_path / "runs.json"
        await promote_run(store, RUN_ID, runs_file=runs_file)
        await promote_run(store, "run-def456", runs_file=runs_file)
        loaded = load_recorded_runs(runs_file)
        assert set(loaded) == {RUN_ID, "run-def456"}

    async def test_case_overrides_win(self, tmp_path: Path) -> None:
        store = InMemoryCheckpointStore()
        await store.save(_make_checkpoint())
        cases_dir = tmp_path / "cases"
        await promote_run(
            store,
            RUN_ID,
            runs_file=tmp_path / "runs.json",
            cases_dir=cases_dir,
            case_overrides={
                "max_tool_calls": 9,
                "expected_keywords": ["Q3 report"],
            },
        )
        case = load_cases(cases_dir)[0]
        assert case["max_tool_calls"] == 9
        assert case["expected_keywords"] == ["Q3 report"]
        assert case["expected_tools"] == ["search_knowledge_base", "scrape_url"]

    async def test_rejects_override_keys_loader_would_refuse(
        self, tmp_path: Path
    ) -> None:
        store = InMemoryCheckpointStore()
        await store.save(_make_checkpoint())
        with pytest.raises(PromotionError, match="override"):
            await promote_run(
                store,
                RUN_ID,
                runs_file=tmp_path / "runs.json",
                cases_dir=tmp_path / "cases",
                case_overrides={"bogus_key": 1},
            )

    async def test_rejects_case_id_override(self, tmp_path: Path) -> None:
        store = InMemoryCheckpointStore()
        await store.save(_make_checkpoint())
        with pytest.raises(PromotionError, match="case_id"):
            await promote_run(
                store,
                RUN_ID,
                runs_file=tmp_path / "runs.json",
                cases_dir=tmp_path / "cases",
                case_overrides={"case_id": "renamed"},
            )

    async def test_case_yaml_is_a_top_level_list(self, tmp_path: Path) -> None:
        """The corpus ratchet counts YAML files as top-level lists."""
        store = InMemoryCheckpointStore()
        await store.save(_make_checkpoint())
        result = await promote_run(
            store,
            RUN_ID,
            runs_file=tmp_path / "runs.json",
            cases_dir=tmp_path / "cases",
        )
        assert result.case_path is not None
        data = yaml.safe_load(result.case_path.read_text(encoding="utf-8"))
        assert isinstance(data, list) and len(data) == 1

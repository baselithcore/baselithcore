"""Promotion of recorded production runs into the eval corpus.

The durable checkpoint store (:mod:`core.orchestration.checkpoint`) already
persists everything a regression recording needs — query, final answer, and
the ordered tool trajectory. This module exploits that: it converts a
*completed* checkpoint into the exact JSON shape
:func:`core.evaluation.regression_runner.load_recorded_runs` replays, and can
emit a starter :class:`~core.evaluation.trajectory.TrajectoryCase` alongside
it, so real production behavior becomes a deterministic CI fixture.

Every text field crosses a **scrub step** first (:func:`scrub_text`): PII is
redacted via :class:`~core.guardrails.output_guard.OutputGuard` and
indirect-injection artifacts (zero-width characters, bidi overrides,
instruction-bearing HTML comments) are stripped via the
:class:`~core.guardrails.indirect.IndirectInjectionScanner` sanitizer. The
same scrubber gates the fine-tuning sample buffer
(:mod:`core.learning.auto_finetuning`), so neither the eval corpus nor
training data can inherit secrets or a poisoned trace from production.

Promotion fails closed: unknown runs, runs that did not complete, duplicate
run ids, and case overrides the regression loader would reject all raise
:class:`PromotionError` before anything is written.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

import yaml

from core.evaluation.regression_runner import ALLOWED_CASE_KEYS
from core.guardrails.config import GuardrailsConfig
from core.guardrails.indirect import IndirectInjectionScanner
from core.guardrails.output_guard import OutputGuard
from core.observability.logging import get_logger

if TYPE_CHECKING:
    from core.orchestration.checkpoint import CheckpointStore

logger = get_logger(__name__)

#: Slack added to the observed tool-call count when deriving the starter
#: case's ``max_tool_calls`` budget, so a marginally longer-but-still-sane
#: future run does not fail the promoted case outright.
CASE_TOOL_CALL_SLACK = 2

# Scrub-note prefixes. Notes look like ``pii:email`` / ``indirect:zero_width``.
_PII_NOTE_PREFIX = "pii"
_INDIRECT_NOTE_PREFIX = "indirect"

_scanner = IndirectInjectionScanner()
_output_guard: OutputGuard | None = None


class PromotionError(RuntimeError):
    """Raised when a run cannot be promoted into the eval corpus."""


@dataclass(frozen=True)
class PromotionResult:
    """Outcome of one :func:`promote_run` call.

    Attributes:
        run_id: The promoted checkpoint's run id (also the recorded run's
            ``case_id``).
        scrubbed: Deduplicated scrub notes applied across all text fields,
            e.g. ``["pii:email", "indirect:zero_width"]``. Empty when the
            content was clean.
        case_path: Path of the starter case YAML, or ``None`` when no
            ``cases_dir`` was given.
    """

    run_id: str
    scrubbed: list[str]
    case_path: Path | None = None


def _get_output_guard() -> OutputGuard:
    """PII-only output guard, built lazily and reused across calls.

    Harmful-content rewriting and length truncation are deliberately off:
    the scrub step must redact secrets, not editorialize recorded answers.
    """
    global _output_guard
    if _output_guard is None:
        _output_guard = OutputGuard(
            GuardrailsConfig(
                output_enabled=True,
                filter_pii=True,
                filter_harmful_content=False,
                max_output_length=10_000_000,
            )
        )
    return _output_guard


def scrub_text(text: str) -> tuple[str, list[str]]:
    """Scrub one string for corpus/training use. Deterministic, no LLM.

    Applies :class:`OutputGuard` PII redaction (emails, phones, SSNs, cards,
    IBANs, ...) and then the indirect-injection scan with sanitizing enabled
    (zero-width/bidi characters and HTML comments stripped when flagged).
    Visible directive phrases are *reported* (``indirect:ai_directive``) but
    not rewritten — dropping such content is the caller's policy decision.

    Args:
        text: The raw text to scrub.

    Returns:
        Tuple of ``(clean_text, notes)`` where ``notes`` lists what was
        applied, e.g. ``["pii:email", "indirect:zero_width"]``.
    """
    if not text:
        return text, []
    notes: list[str] = []
    filtered = _get_output_guard().filter(text)
    cleaned = filtered.filtered_output
    for pii_type in sorted(filtered.redactions or {}):
        notes.append(f"{_PII_NOTE_PREFIX}:{pii_type}")
    scan = _scanner.scan(cleaned)
    if scan.is_suspicious:
        for finding in scan.findings:
            note = f"{_INDIRECT_NOTE_PREFIX}:{finding.kind.value}"
            if note not in notes:
                notes.append(note)
        cleaned = _scanner.sanitize(cleaned)
    return cleaned, notes


def _merge_notes(dest: list[str], src: list[str]) -> None:
    """Append ``src`` notes into ``dest``, preserving order, deduplicated."""
    for note in src:
        if note not in dest:
            dest.append(note)


def _scrub_value(value: Any, notes: list[str]) -> Any:
    """Recursively scrub every string inside a JSON-shaped value."""
    if isinstance(value, str):
        cleaned, applied = scrub_text(value)
        _merge_notes(notes, applied)
        return cleaned
    if isinstance(value, dict):
        return {key: _scrub_value(item, notes) for key, item in value.items()}
    if isinstance(value, list):
        return [_scrub_value(item, notes) for item in value]
    return value


def _step_observation(steps: Any, entry: dict[str, Any]) -> Any | None:
    """Recorded result for one trajectory entry, if the checkpoint holds it.

    Step keys are ``"{cursor}:{tool}:{args_hash}"`` (see
    :func:`core.orchestration.checkpoint.step_key`); cursor + tool identify
    the entry without re-hashing the args.
    """
    if not isinstance(steps, dict):
        return None
    cursor = entry.get("cursor")
    tool = entry.get("tool") or entry.get("tool_name") or entry.get("name")
    if cursor is None or not tool:
        return None
    prefix = f"{cursor}:{tool}:"
    for key, recorded in steps.items():
        if (
            isinstance(key, str)
            and key.startswith(prefix)
            and isinstance(recorded, dict)
        ):
            return recorded.get("result")
    return None


def checkpoint_to_recorded_run(
    checkpoint_dict: dict[str, Any],
    *,
    notes: list[str] | None = None,
) -> dict[str, Any]:
    """Map a persisted checkpoint dict to the recorded-run JSON shape.

    Produces exactly what
    :func:`core.evaluation.regression_runner.load_recorded_runs` replays:
    ``case_id`` (the run id), ``output_text``, ``trajectory`` (tool calls with
    ``name``/``args``), ``latency_ms`` (derived from the checkpoint's
    created/updated timestamps) and ``cost_usd`` (from the budget snapshot).
    The scrubbed ``query`` and per-call ``observation`` fields ride along as
    provenance; the loader ignores what it does not read.

    Args:
        checkpoint_dict: ``Checkpoint.to_dict()`` output (or the raw persisted
            row).
        notes: Optional accumulator that collects the deduplicated scrub
            notes applied across query, answer, args and observations.

    Returns:
        A JSON-serializable dict ready to append to a recorded-runs file.

    Raises:
        PromotionError: If the checkpoint has no ``run_id``.
    """
    collected: list[str] = [] if notes is None else notes
    run_id = str(checkpoint_dict.get("run_id") or "")
    if not run_id:
        raise PromotionError("checkpoint has no run_id; cannot become a case_id")

    answer = checkpoint_dict.get("answer")
    output_text, answer_notes = scrub_text("" if answer is None else str(answer))
    _merge_notes(collected, answer_notes)
    query, query_notes = scrub_text(str(checkpoint_dict.get("query") or ""))
    _merge_notes(collected, query_notes)

    steps = checkpoint_dict.get("steps") or {}
    trajectory: list[dict[str, Any]] = []
    for entry in checkpoint_dict.get("trajectory") or []:
        if not isinstance(entry, dict):
            continue
        call: dict[str, Any] = {}
        tool = entry.get("tool") or entry.get("tool_name") or entry.get("name")
        if isinstance(tool, str) and tool:
            call["name"] = tool
        args = entry.get("args")
        if isinstance(args, dict):
            call["args"] = _scrub_value(args, collected)
        observation = _step_observation(steps, entry)
        if observation is not None:
            call["observation"] = _scrub_value(observation, collected)
        trajectory.append(call)

    latency_ms = 0
    created = checkpoint_dict.get("created_at")
    updated = checkpoint_dict.get("updated_at")
    if (
        isinstance(created, (int, float))
        and isinstance(updated, (int, float))
        and updated > created
    ):
        latency_ms = int((updated - created) * 1000)

    budget = checkpoint_dict.get("budget")
    cost_usd = 0.0
    if isinstance(budget, dict):
        try:
            cost_usd = float(budget.get("cost_usd", 0.0))
        except (TypeError, ValueError):
            cost_usd = 0.0

    return {
        "case_id": run_id,
        "query": query,
        "output_text": output_text,
        "trajectory": trajectory,
        "latency_ms": latency_ms,
        "cost_usd": cost_usd,
    }


def _load_runs_list(runs_file: Path) -> list[Any]:
    """Existing recorded runs (empty when the file does not exist yet)."""
    if not runs_file.exists():
        return []
    try:
        data = json.loads(runs_file.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise PromotionError(f"{runs_file} is not valid JSON: {exc}") from exc
    if not isinstance(data, list):
        raise PromotionError(f"{runs_file}: top-level must be a JSON list")
    return data


def _build_starter_case(
    run_dict: dict[str, Any], overrides: dict[str, Any]
) -> dict[str, Any]:
    """Starter ``TrajectoryCase`` mapping for a promoted run.

    Derived from what actually happened — expected tools are the tools the
    run really used, and the call budget is the observed count plus
    :data:`CASE_TOOL_CALL_SLACK`. Caller overrides win, but only for keys the
    regression loader accepts, and ``case_id`` stays bound to the run id so
    the case/recording pair cannot drift apart.
    """
    if "case_id" in overrides:
        raise PromotionError(
            "case_id override is not allowed: the case must keep the run id "
            "so it stays paired with its recorded run"
        )
    unknown = set(overrides) - set(ALLOWED_CASE_KEYS)
    if unknown:
        raise PromotionError(
            "case overrides use keys the regression loader rejects: "
            f"{sorted(unknown)} (allowed: {sorted(ALLOWED_CASE_KEYS)})"
        )
    trajectory = run_dict.get("trajectory") or []
    expected_tools: list[str] = []
    for call in trajectory:
        name = call.get("name")
        if isinstance(name, str) and name and name not in expected_tools:
            expected_tools.append(name)
    case: dict[str, Any] = {
        "case_id": run_dict["case_id"],
        "input": run_dict.get("query", ""),
        "max_tool_calls": len(trajectory) + CASE_TOOL_CALL_SLACK,
    }
    if expected_tools:
        case["expected_tools"] = expected_tools
    case.update(overrides)
    return case


async def promote_run(
    store: CheckpointStore,
    run_id: str,
    *,
    runs_file: Path,
    cases_dir: Path | None = None,
    case_overrides: dict[str, Any] | None = None,
) -> PromotionResult:
    """Promote one completed production run into the eval corpus.

    Loads the checkpoint from ``store``, scrubs it, appends the replayable
    recording to ``runs_file`` (a JSON list, created if missing) and — when
    ``cases_dir`` is given — writes a starter trajectory-case YAML named
    ``<run_id>.yaml`` (a single-element top-level list, so the corpus ratchet
    counts it). All validation happens before the first byte is written.

    Args:
        store: Any :class:`~core.orchestration.checkpoint.CheckpointStore`.
        run_id: The checkpoint run id to promote.
        runs_file: Recorded-runs JSON file to append to.
        cases_dir: Optional directory to write the starter case into.
        case_overrides: Optional case fields that win over the derived
            defaults (``case_id`` excluded; keys must be ones the regression
            loader accepts).

    Returns:
        A :class:`PromotionResult` with the applied scrub notes.

    Raises:
        PromotionError: Unknown run, non-completed status, duplicate run id,
            malformed runs file, pre-existing case file, or invalid overrides.
    """
    from core.orchestration.checkpoint import STATUS_COMPLETED

    checkpoint = await store.load(run_id)
    if checkpoint is None:
        raise PromotionError(f"run {run_id!r} not found in the checkpoint store")
    if checkpoint.status != STATUS_COMPLETED:
        raise PromotionError(
            f"run {run_id!r} has status {checkpoint.status!r}; only "
            f"{STATUS_COMPLETED!r} runs can be promoted (fail closed)"
        )

    notes: list[str] = []
    run_dict = checkpoint_to_recorded_run(checkpoint.to_dict(), notes=notes)

    runs = _load_runs_list(runs_file)
    if any(isinstance(r, dict) and r.get("case_id") == run_id for r in runs):
        raise PromotionError(f"run {run_id!r} is already recorded in {runs_file}")

    case_payload: dict[str, Any] | None = None
    case_path: Path | None = None
    if cases_dir is not None:
        case_payload = _build_starter_case(run_dict, case_overrides or {})
        case_path = cases_dir / f"{run_id}.yaml"
        if case_path.exists():
            raise PromotionError(
                f"case file {case_path} already exists; refusing to overwrite"
            )

    runs.append(run_dict)
    runs_file.parent.mkdir(parents=True, exist_ok=True)
    runs_file.write_text(
        json.dumps(runs, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    if case_payload is not None and case_path is not None:
        case_path.parent.mkdir(parents=True, exist_ok=True)
        case_path.write_text(
            yaml.safe_dump([case_payload], sort_keys=True, allow_unicode=True),
            encoding="utf-8",
        )

    logger.info(
        "run_promoted run=%s runs_file=%s case=%s scrubbed=%s",
        run_id,
        runs_file,
        case_path,
        notes or "clean",
    )
    return PromotionResult(run_id=run_id, scrubbed=notes, case_path=case_path)


__all__ = [
    "CASE_TOOL_CALL_SLACK",
    "PromotionError",
    "PromotionResult",
    "checkpoint_to_recorded_run",
    "promote_run",
    "scrub_text",
]

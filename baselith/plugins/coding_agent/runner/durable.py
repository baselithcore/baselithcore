"""Durable model answers and sandbox runs for a checkpointed run.

A coding task is a loop of expensive steps — a typed model answer per attempt and a sandbox
test run per candidate. When the run has a checkpoint (the orchestrator binds the run's
:class:`~baselith.core.orchestration.checkpoint.CheckpointManager` as ambient while a handler
runs), each of those steps is recorded under a coding-agent child scope, so a run resumed after
a crash replays the answers and test reports it already paid for and continues from the first
step that never finished. Without a checkpoint nothing changes.

The keys are content-addressed: an answer is keyed by a digest of the prompt and the answer
schema, a test run by a digest of the code and the tests. The loop is deterministic given those
results (the lessons and evidence each prompt carries come from the recorded reports), so the
resumed pass asks the same prompts in the same order and lands on the recorded keys; a prompt
that differs for any reason simply misses and runs for real.
"""

from __future__ import annotations

import hashlib
from collections.abc import Awaitable, Callable
from dataclasses import asdict
from typing import Any, TypeVar

from pydantic import BaseModel

from baselith.core.orchestration.checkpoint_scope import current_checkpoint

from .harness import TestReport

M = TypeVar("M", bound=BaseModel)


def digest(text: str) -> str:
    """Short stable digest of ``text`` (keys and identities, never the text itself)."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def coding_scope(task: str, identity: dict[str, Any]) -> Any | None:
    """A child scope of the ambient checkpoint for one coding task, or ``None``."""
    checkpoint = current_checkpoint()
    if checkpoint is None:
        return None
    return checkpoint.scope("coding_agent", {"task": task, **identity})


async def durable_ask(
    scope: Any | None,
    prompt: str,
    model: type[M],
    ask: Callable[[str, type[M]], Awaitable[M]],
) -> M:
    """``ask(prompt, model)``, recorded as a step when ``scope`` is set."""
    if scope is None:
        return await ask(prompt, model)

    async def _answer() -> dict[str, Any]:
        return (await ask(prompt, model)).model_dump(mode="json")

    recorded = await scope.run_step(
        "ask",
        {"model": model.__name__, "prompt": digest(prompt)},
        _answer,
        category="llm",
    )
    return model.model_validate(recorded)


def report_to_dict(report: TestReport) -> dict[str, Any]:
    """A JSON-safe copy of ``report``."""
    data = asdict(report)
    data["failures"] = [list(item) for item in report.failures]
    return data


def report_from_dict(data: dict[str, Any]) -> TestReport:
    """The :class:`TestReport` a recorded step stored."""
    fields = dict(data)
    fields["failures"] = tuple(
        (str(name), str(text)) for name, text in fields.get("failures") or ()
    )
    return TestReport(**fields)


async def durable_tests(
    scope: Any | None,
    code: str,
    tests: str,
    run: Callable[[str, str], Awaitable[TestReport]],
) -> TestReport:
    """``run(code, tests)``, recorded as a step when ``scope`` is set."""
    if scope is None:
        return await run(code, tests)

    async def _run() -> dict[str, Any]:
        return report_to_dict(await run(code, tests))

    recorded = await scope.run_step(
        "run_tests",
        {"code": digest(code), "tests": digest(tests)},
        _run,
        category="sandbox",
    )
    return report_from_dict(recorded)


__all__ = [
    "coding_scope",
    "digest",
    "durable_ask",
    "durable_tests",
    "report_from_dict",
    "report_to_dict",
]

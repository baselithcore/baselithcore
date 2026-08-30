"""Hierarchical (manager-led) crew process.

Companion module to :mod:`core.agent.crew` implementing
``Crew(process="hierarchical")``: a ``manager`` agent coordinates each task
in order —

1. **Delegate** — the manager receives the task prompt (description, inputs,
   prior-task context) and writes a short delegation brief, which is appended
   to the worker's prompt.
2. **Execute** — the assigned worker agent runs the task.
3. **Review** — the manager reviews the worker's output and returns a strict
   JSON verdict (reasoning first): ``APPROVED`` or ``REVISE`` with feedback.
4. **Revise (bounded)** — on ``REVISE`` the task is re-run exactly once with
   the feedback appended; the second output is accepted regardless, and the
   task result is flagged ``review="revised"``. There are no review loops.

Any manager LLM failure (brief, review call, or malformed review JSON) fails
open: the worker output is accepted as ``APPROVED`` and a warning is logged.
Coordination is never allowed to block delivery.

The verdict enum mirrors the ``APPROVED``/``REVISE`` shape of
:class:`core.meta.generator_challenger.Verdict` but is defined locally: the
review here is deliberately two-outcome and single-round (no ``REJECT``, no
multi-round judge), and ``core.agent`` stays decoupled from ``core.meta``.
"""

from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass
from enum import Enum
from typing import TYPE_CHECKING, Any

from core.agent.crew import CrewResult, TaskResult
from core.observability.logging import get_logger

if TYPE_CHECKING:
    from core.agent.agent import Agent
    from core.agent.crew import Crew, Task

logger = get_logger(__name__)

_FENCE_RE = re.compile(r"^```(?:json)?\s*|\s*```$", re.MULTILINE)


class ReviewVerdict(str, Enum):
    """Manager verdict on a worker's task output."""

    APPROVED = "approved"
    REVISE = "revise"


@dataclass(frozen=True)
class ReviewDecision:
    """Parsed outcome of one manager review turn."""

    verdict: ReviewVerdict
    feedback: str = ""


def _brief_prompt(task_prompt: str) -> str:
    return (
        "You are the crew manager. Delegate the following task to the "
        "assigned specialist. Write a short, actionable delegation brief: "
        "the goal, key constraints, and what a strong deliverable looks "
        f"like.\n\nTask:\n{task_prompt}\n\nReply with the brief only."
    )


def _review_prompt(task_prompt: str, worker_text: str) -> str:
    return (
        "You are the crew manager reviewing a specialist's deliverable.\n\n"
        f"Task:\n{task_prompt}\n\nDeliverable:\n{worker_text}\n\n"
        "Reply with STRICT JSON only, reasoning first, in exactly this "
        "shape:\n"
        '{"reasoning": "<your assessment>", '
        '"verdict": "APPROVED" | "REVISE", '
        '"feedback": "<what to change — required when verdict is REVISE>"}'
    )


async def _manager_brief(manager: Agent[Any], task_prompt: str) -> str | None:
    """Ask the manager for a delegation brief; fail open to ``None``."""
    try:
        result = await manager.run(_brief_prompt(task_prompt))
    except Exception as exc:
        logger.warning(f"crew manager brief failed, delegating without one: {exc}")
        return None
    return result.text or None


def _parse_review(text: str) -> ReviewDecision:
    """Parse the manager's strict-JSON review; raise on any malformation."""
    payload = json.loads(_FENCE_RE.sub("", text.strip()).strip())
    verdict = str(payload["verdict"]).strip().upper()
    if verdict == ReviewVerdict.REVISE.name:
        feedback = str(payload.get("feedback") or payload.get("reasoning") or "")
        return ReviewDecision(ReviewVerdict.REVISE, feedback)
    if verdict == ReviewVerdict.APPROVED.name:
        return ReviewDecision(ReviewVerdict.APPROVED)
    raise ValueError(f"unknown review verdict {verdict!r}")


async def _manager_review(
    manager: Agent[Any], task_prompt: str, worker_text: str
) -> ReviewDecision:
    """Ask the manager to review; fail open to ``APPROVED`` on any failure."""
    try:
        result = await manager.run(_review_prompt(task_prompt, worker_text))
        return _parse_review(result.text)
    except Exception as exc:
        logger.warning(f"crew manager review failed, failing open to APPROVED: {exc}")
        return ReviewDecision(ReviewVerdict.APPROVED)


async def _run_hierarchical_task(
    crew: Crew,
    index: int,
    task: Task,
    inputs: dict[str, Any],
    context: list[tuple[str, str]],
) -> TaskResult:
    """Run one delegate → execute → review (→ one revision) cycle."""
    assert crew.manager is not None  # guaranteed by Crew.__init__
    assert task.agent is not None  # guaranteed by Crew.__init__
    name = task.name or f"task-{index}"
    base_prompt = crew._task_prompt(task, inputs, context)
    logger.debug("crew_task_start name=%s process=hierarchical", name)

    start = time.perf_counter()
    brief = await _manager_brief(crew.manager, base_prompt)
    worker_prompt = base_prompt
    if brief:
        worker_prompt += f"\n\nDelegation brief from the crew manager:\n{brief}"

    result = await task.agent.run(worker_prompt)
    review_flag = ReviewVerdict.APPROVED.value
    decision = await _manager_review(crew.manager, base_prompt, result.text)
    if decision.verdict is ReviewVerdict.REVISE:
        # Bounded: exactly one revision round; the second output is accepted
        # without further review.
        revision_prompt = (
            f"{worker_prompt}\n\nReviewer feedback — address it and produce "
            f"the final version:\n{decision.feedback}"
        )
        result = await task.agent.run(revision_prompt)
        review_flag = "revised"

    latency_ms = int((time.perf_counter() - start) * 1000)
    return TaskResult(
        name=name,
        output=result.output,
        text=result.text,
        agent_index=crew._agent_index(task.agent),
        latency_ms=latency_ms,
        cost_usd=crew._task_cost(task, result.output),
        review=review_flag,
    )


async def run_hierarchical(crew: Crew, inputs: dict[str, Any]) -> CrewResult:
    """Execute a crew's task list under the hierarchical process.

    Tasks run in order; each accepted output threads into the next task's
    context exactly as in the sequential process. Per-task ``latency_ms``
    covers the full cycle (manager turns included — the coordination tax).

    Args:
        crew: The crew (``crew.manager`` must be set — enforced by
            ``Crew.__init__``).
        inputs: Template inputs for task descriptions.

    Returns:
        The :class:`~core.agent.crew.CrewResult` with per-task results in
        task order.
    """
    context: list[tuple[str, str]] = []
    task_results: list[TaskResult] = []
    for index, task in enumerate(crew.tasks):
        task_result = await _run_hierarchical_task(crew, index, task, inputs, context)
        task_results.append(task_result)
        context.append((task_result.name, task_result.text))
    return CrewResult(task_results=task_results)


__all__ = ["ReviewDecision", "ReviewVerdict", "run_hierarchical"]

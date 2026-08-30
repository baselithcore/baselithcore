"""Loop engineering primitives.

The system — not the human — owns the iteration: a machine-checkable
verifier decides when the goal is met, a stall guard detects futility, the
lesson log compacts failures into feed-forward context, and escalation hands
a resumable state to a human when the loop loses.

Production wiring: :class:`~core.loops.flow_handler.LoopFlowHandler`
registers a loop as an orchestrator flow, with durable outcome persistence,
per-attempt heartbeats, run-event progress and a default escalation sink
(:func:`~core.loops.escalation.build_default_escalation`). Soft goals can be
verified with :func:`~core.loops.rubric.rubric_verifier`, and raw goals
interrogated pre-flight via :func:`~core.loops.goal.harden_goal`.
"""

from core.loops.engineered import (
    AttemptContext,
    EngineeredLoop,
    LoopOutcome,
    LoopStatus,
)
from core.loops.escalation import LOOP_ESCALATED_EVENT, build_default_escalation
from core.loops.fingerprint import FAILURE_MARKERS, failure_fingerprint, failure_lines
from core.loops.flow_handler import LoopFlowHandler
from core.loops.goal import HardenedGoal, harden_goal
from core.loops.lessons import Lesson, LessonLog, compact_evidence
from core.loops.rubric import RubricJudge, rubric_verifier
from core.loops.stall import StallGuard, StallVerdict

__all__ = [
    "FAILURE_MARKERS",
    "LOOP_ESCALATED_EVENT",
    "AttemptContext",
    "EngineeredLoop",
    "HardenedGoal",
    "Lesson",
    "LessonLog",
    "LoopFlowHandler",
    "LoopOutcome",
    "LoopStatus",
    "RubricJudge",
    "StallGuard",
    "StallVerdict",
    "build_default_escalation",
    "compact_evidence",
    "failure_fingerprint",
    "failure_lines",
    "harden_goal",
    "rubric_verifier",
]

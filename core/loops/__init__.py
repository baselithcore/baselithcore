"""Loop engineering primitives.

The system — not the human — owns the iteration: a machine-checkable
verifier decides when the goal is met, a stall guard detects futility, the
lesson log compacts failures into feed-forward context, and escalation hands
a resumable state to a human when the loop loses.
"""

from core.loops.engineered import (
    AttemptContext,
    EngineeredLoop,
    LoopOutcome,
    LoopStatus,
)
from core.loops.fingerprint import FAILURE_MARKERS, failure_fingerprint, failure_lines
from core.loops.lessons import Lesson, LessonLog, compact_evidence
from core.loops.stall import StallGuard, StallVerdict

__all__ = [
    "FAILURE_MARKERS",
    "AttemptContext",
    "EngineeredLoop",
    "Lesson",
    "LessonLog",
    "LoopOutcome",
    "LoopStatus",
    "StallGuard",
    "StallVerdict",
    "compact_evidence",
    "failure_fingerprint",
    "failure_lines",
]

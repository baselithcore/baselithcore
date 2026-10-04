"""Golden cassettes — the implementation moved to :mod:`core.evaluation.cassette`.

The eval regression gate needs the same replay machinery, and ``core`` cannot
import from the test tree. Rather than keep two copies in step, the machinery
moved and this module re-exports it, so every existing
``from tests.golden.cassette import ...`` keeps working.
"""

from pathlib import Path

from core.evaluation.cassette import (
    Cassette,
    CassetteMismatch,
    Expect,
    RecordedLLMService,
    RecordingLLMService,
    Turn,
)

#: The golden cassettes, anchored to this file so they load from any cwd
#: (core's default is cwd-relative, since it must never point into an
#: installed package).
CASSETTE_DIR = Path(__file__).resolve().parent / "cassettes"

__all__ = [
    "CASSETTE_DIR",
    "Cassette",
    "CassetteMismatch",
    "Expect",
    "RecordedLLMService",
    "RecordingLLMService",
    "Turn",
]

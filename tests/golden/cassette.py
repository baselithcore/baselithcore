"""Golden cassettes — the implementation moved to :mod:`core.evaluation.cassette`.

The eval regression gate needs the same replay machinery, and ``core`` cannot
import from the test tree. Rather than keep two copies in step, the machinery
moved and this module re-exports it, so every existing
``from tests.golden.cassette import ...`` keeps working.
"""

from core.evaluation.cassette import (
    CASSETTE_DIR,
    Cassette,
    CassetteMismatch,
    Expect,
    RecordedLLMService,
    RecordingLLMService,
    Turn,
)

__all__ = [
    "CASSETTE_DIR",
    "Cassette",
    "CassetteMismatch",
    "Expect",
    "RecordedLLMService",
    "RecordingLLMService",
    "Turn",
]

"""The workspace a repository fix runs in, and what the fix returns."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from plugins.coding_agent.types import CodingResult

#: Why a fix came back without running its tests.
UNVERIFIED_REASONS = (
    "language",
    "missing_dependency",
    "sandbox_unavailable",
    "too_large",
    "probe_failed",
)


@dataclass(frozen=True, slots=True)
class Workspace:
    """Repository-relative path to text: ``target`` is fixed, the other files are context.

    Attributes:
        files: Every file the fix may read, the target included.
        target: The one file the fix changes.
        test_path: Where the new test file goes; it is not one of ``files``.
        roots: Import roots relative to the repository (``""``, plus ``"src"`` for a src
            layout).
    """

    files: Mapping[str, str]
    target: str
    test_path: str
    roots: tuple[str, ...] = ("",)

    def __post_init__(self) -> None:
        if self.target not in self.files:
            raise ValueError("the target must be one of the files")
        if self.test_path in self.files:
            raise ValueError("the test path must be a new file")


@dataclass
class WorkspaceResult(CodingResult):
    """A :class:`CodingResult` for one repository file.

    ``verified`` is True when the tests ran in the sandbox (passed or not); otherwise
    ``unverified_reason`` is one of :data:`UNVERIFIED_REASONS`.
    """

    target: str = ""
    test_path: str = ""
    verified: bool = False
    unverified_reason: str = ""
    #: Unified diff of the target, original to final.
    diff: str = ""


__all__ = ["UNVERIFIED_REASONS", "Workspace", "WorkspaceResult"]

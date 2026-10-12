"""What the model answers with: validated Pydantic shapes, never free text to fish code out of."""

from __future__ import annotations

from pydantic import BaseModel, Field


class CodeAnswer(BaseModel):
    """New code for an existing test file (fix and refactor after the first attempt)."""

    code: str = Field(description="The complete solution.py source, nothing else.")
    explanation: str = Field(description="One or two sentences: what changed and why.")
    changes: list[str] = Field(description="Each change as one short line.")


class CodeWithTests(BaseModel):
    """Code plus the pytest tests that prove it (fix and write, first attempt)."""

    code: str = Field(description="The complete solution.py source, nothing else.")
    tests: str = Field(
        description="A complete pytest file that imports from solution (from solution import ...)."
    )
    explanation: str = Field(
        description="One or two sentences: what the code does or fixes."
    )
    changes: list[str] = Field(description="Each change as one short line.")


class TestsAnswer(BaseModel):
    """A pytest file for code that already exists (tests, and refactor's safety net)."""

    __test__ = False  # not a pytest test class, whatever its name

    tests: str = Field(
        description="A complete pytest file that imports from solution (from solution import ...)."
    )
    explanation: str = Field(description="One or two sentences: what the tests cover.")


class WorkspaceFix(BaseModel):
    """A fixed file of a repository and the test file that proves it."""

    code: str = Field(description="The complete new text of the file being fixed.")
    tests: str = Field(
        description="A complete test file that imports the fixed module by its real import path."
    )
    explanation: str = Field(
        description="Why the bug happened and what the fix changes."
    )
    changes: list[str] = Field(
        default_factory=list, description="Each change as one short line."
    )


class WorkspaceCode(BaseModel):
    """A fixed file of a repository; the tests are already fixed."""

    code: str = Field(description="The complete new text of the file being fixed.")
    explanation: str = Field(description="What the fix changes.")
    changes: list[str] = Field(
        default_factory=list, description="Each change as one short line."
    )


__all__ = [
    "CodeAnswer",
    "CodeWithTests",
    "TestsAnswer",
    "WorkspaceCode",
    "WorkspaceFix",
]

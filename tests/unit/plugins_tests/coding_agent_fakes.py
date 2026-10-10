"""Fakes for the coding agent: a scripted model and a sandbox that really runs the harness.

``LocalSandbox`` executes the script the agent would send to the container with this
interpreter (pytest is installed here as in the sandbox image), so the harness, the report
parsing and the verified loop are exercised for real, without Docker.
"""

from __future__ import annotations

import asyncio
import json
import subprocess
import sys
import time
from collections.abc import Iterable
from types import SimpleNamespace
from typing import Any

from core.services.llm.tool_calling import LLMResult


class LocalSandbox:
    """``SandboxService.execute_code_async`` with a local ``python -c``."""

    def __init__(self) -> None:
        self.calls = 0

    async def execute_code_async(
        self, code: str, language: str = "python", timeout: int | None = None
    ) -> Any:
        self.calls += 1
        started = time.monotonic()
        proc = await asyncio.to_thread(
            subprocess.run,
            [sys.executable, "-c", code],
            capture_output=True,
            text=True,
            timeout=timeout or 60,
            check=False,
        )
        return SimpleNamespace(
            stdout=proc.stdout,
            stderr=proc.stderr,
            exit_code=proc.returncode,
            execution_time=time.monotonic() - started,
        )


class ScriptedLLM:
    """Answers each call with the next scripted payload (a dict is sent as JSON text)."""

    def __init__(self, answers: Iterable[Any]) -> None:
        self.answers = list(answers)
        self.prompts: list[str] = []
        self.systems: list[str] = []

    async def generate(self, prompt: str = "", *args: Any, **kwargs: Any) -> LLMResult:
        self.prompts.append(prompt or str(kwargs.get("prompt", "")))
        self.systems.append(str(kwargs.get("system_prompt", "")))
        if not self.answers:
            raise AssertionError("the scripted model ran out of answers")
        answer = self.answers.pop(0)
        if isinstance(answer, Exception):
            raise answer
        text = json.dumps(answer) if isinstance(answer, dict) else str(answer)
        return LLMResult(text=text)


GOOD_ADD = "def add(a: int, b: int) -> int:\n    return a + b\n"
BAD_ADD = "def add(a: int, b: int) -> int:\n    return a - b\n"
ADD_TESTS = (
    "from solution import add\n\n\n"
    "def test_adds() -> None:\n    assert add(2, 3) == 5\n\n\n"
    "def test_zero() -> None:\n    assert add(0, 0) == 0\n"
)

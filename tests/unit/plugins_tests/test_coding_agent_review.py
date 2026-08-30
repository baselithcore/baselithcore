"""Coding Agent x deterministic security review.

The agent must never ship a credential: a ``high``-severity review replaces
the generated code with a refusal note listing the findings, while
``medium`` findings ride along on the result's ``review`` field without
blocking. LLM and sandbox are stubbed — no network, no Docker.
"""

from __future__ import annotations

from types import SimpleNamespace

from plugins.coding_agent import CodingAgent

# NOTE: fixture strings below are deliberately "leaked" fake credentials and
# insecure snippets — they exist to prove the reviewer blocks/annotates them.
_SECRET_CODE = 'import boto3\nkey = "AKIAIOSFODNN7EXAMPLE"\nprint(key)\n'
_MEDIUM_CODE = "import requests\nresp = requests.get(url, verify=False)\n"
_CLEAN_CODE = "def add(a: int, b: int) -> int:\n    return a + b\n"


class _StubLLM:
    """LLM stub returning a fixed fenced code block."""

    def __init__(self, code: str) -> None:
        self._code = code

    async def generate(self, **_kwargs: object) -> SimpleNamespace:
        return SimpleNamespace(content=f"```python\n{self._code}\n```")


class _StubSandbox:
    """Sandbox stub that always reports a successful execution."""

    async def execute_code_async(self, **_kwargs: object) -> SimpleNamespace:
        return SimpleNamespace(exit_code=0, stdout="", stderr="", execution_time=0.01)


def _agent(code: str) -> CodingAgent:
    agent = CodingAgent()
    agent._llm = _StubLLM(code)
    agent._sandbox = _StubSandbox()
    return agent


async def test_generated_code_with_secret_is_withheld() -> None:
    result = await _agent(_SECRET_CODE).generate_code("read a bucket")

    assert result.success is False
    assert "AKIAIOSFODNN7EXAMPLE" not in result.final_code
    assert "withheld" in result.final_code.lower()
    assert "AWS" in result.final_code  # refusal note lists the findings
    assert result.review is not None
    assert result.review.verdict == "flagged"
    assert result.review.severity == "high"
    assert result.error is not None


async def test_medium_findings_ride_along_without_blocking() -> None:
    result = await _agent(_MEDIUM_CODE).generate_code("fetch a url")

    assert result.success is True
    assert result.final_code == _MEDIUM_CODE.strip()
    assert result.review is not None
    assert result.review.verdict == "flagged"
    assert result.review.severity == "medium"
    assert result.error is None


async def test_clean_generation_carries_approved_review() -> None:
    result = await _agent(_CLEAN_CODE).generate_code("add two numbers")

    assert result.success is True
    assert result.final_code == _CLEAN_CODE.strip()
    assert result.review is not None
    assert result.review.verdict == "approved"
    assert result.review.severity == "none"


async def test_fix_code_loop_withholds_high_severity_fix() -> None:
    agent = _agent(_SECRET_CODE)

    result = await agent.fix_code("print(x", "SyntaxError: unexpected EOF")

    assert result.success is False
    assert "AKIAIOSFODNN7EXAMPLE" not in result.final_code
    assert "withheld" in result.final_code.lower()
    assert result.review is not None
    assert result.review.severity == "high"


async def test_generate_tests_withholds_high_severity_output() -> None:
    result = await _agent(_SECRET_CODE).generate_tests(_CLEAN_CODE)

    assert result.success is False
    assert "AKIAIOSFODNN7EXAMPLE" not in result.final_code
    assert result.review is not None
    assert result.review.severity == "high"

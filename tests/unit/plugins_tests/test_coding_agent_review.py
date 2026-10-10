"""Coding Agent x deterministic security review.

The agent must never ship a credential: a ``high``-severity review replaces
the generated code with a refusal note listing the findings, while
``medium`` findings ride along on the result's ``review`` field without
blocking. The model is scripted and the tests really run (locally), so the
review is checked on code that passed its tests — no network, no Docker.
"""

from __future__ import annotations

from plugins.coding_agent import CodingAgent

from .coding_agent_fakes import LocalSandbox, ScriptedLLM

# NOTE: fixture strings below are deliberately "leaked" fake credentials and
# insecure snippets — they exist to prove the reviewer blocks/annotates them.
_SECRET_CODE = 'KEY = "AKIAIOSFODNN7EXAMPLE"\n\n\ndef key() -> str:\n    return KEY\n'
_SECRET_TESTS = "from solution import key\n\n\ndef test_key():\n    assert key()\n"
_MEDIUM_CODE = (
    "import requests\n\n\ndef fetch(url: str) -> object:\n"
    "    return requests.get(url, verify=False)\n"
)
_MEDIUM_TESTS = (
    "from solution import fetch\n\n\ndef test_callable():\n    assert callable(fetch)\n"
)
_CLEAN_CODE = "def add(a: int, b: int) -> int:\n    return a + b\n"
_CLEAN_TESTS = (
    "from solution import add\n\n\ndef test_add():\n    assert add(2, 3) == 5\n"
)


def _answer(code: str, tests: str) -> dict[str, object]:
    return {"code": code, "tests": tests, "explanation": "done", "changes": []}


def _agent(*answers: object) -> CodingAgent:
    agent = CodingAgent(execution_timeout=60)
    agent._llm = ScriptedLLM(answers)
    agent._sandbox = LocalSandbox()
    return agent


async def test_generated_code_with_secret_is_withheld() -> None:
    result = await _agent(_answer(_SECRET_CODE, _SECRET_TESTS)).generate_code(
        "read a bucket"
    )

    assert result.success is False
    assert "AKIAIOSFODNN7EXAMPLE" not in result.final_code
    assert "withheld" in result.final_code.lower()
    assert "AWS" in result.final_code  # refusal note lists the findings
    assert result.review is not None
    assert result.review.verdict == "flagged"
    assert result.review.severity == "high"
    assert result.error is not None
    assert result.provenance is None  # nothing generated left the agent


async def test_medium_findings_ride_along_without_blocking() -> None:
    result = await _agent(_answer(_MEDIUM_CODE, _MEDIUM_TESTS)).generate_code(
        "fetch a url"
    )

    assert result.success is True
    assert result.final_code == _MEDIUM_CODE
    assert result.review is not None
    assert result.review.verdict == "flagged"
    assert result.review.severity == "medium"
    assert result.error is None


async def test_clean_generation_carries_approved_review() -> None:
    result = await _agent(_answer(_CLEAN_CODE, _CLEAN_TESTS)).generate_code(
        "add two numbers"
    )

    assert result.success is True
    assert result.final_code == _CLEAN_CODE
    assert result.review is not None
    assert result.review.verdict == "approved"
    assert result.review.severity == "none"


async def test_fix_code_loop_withholds_high_severity_fix() -> None:
    agent = _agent(_answer(_SECRET_CODE, _SECRET_TESTS))

    result = await agent.fix_code(
        "def key(:\n    pass\n", "SyntaxError: invalid syntax"
    )

    assert result.success is False
    assert "AKIAIOSFODNN7EXAMPLE" not in result.final_code
    assert "withheld" in result.final_code.lower()
    assert result.review is not None
    assert result.review.severity == "high"


async def test_generate_tests_withholds_high_severity_output() -> None:
    leaky_tests = (
        'TOKEN = "AKIAIOSFODNN7EXAMPLE"\n\n\ndef test_t():\n    assert TOKEN\n'
    )
    agent = _agent({"tests": leaky_tests, "explanation": "covers add"})

    result = await agent.generate_tests(_CLEAN_CODE)

    assert result.success is False
    assert "AKIAIOSFODNN7EXAMPLE" not in result.final_code
    assert result.review is not None
    assert result.review.severity == "high"

"""Tests for pre-flight goal hardening."""

from __future__ import annotations

import json

import pytest
from core.loops.goal import HardenedGoal, harden_goal

pytestmark = [pytest.mark.unit]


class _LLM:
    def __init__(self, payload: str) -> None:
        self._payload = payload

    async def generate_response(self, prompt: str, json: bool = False) -> str:
        return self._payload


class TestHardenedGoal:
    def test_render_carries_all_sections(self):
        goal = HardenedGoal(
            goal="pytest green",
            scope="only core/loops",
            verifier_description="pytest -x returncode 0",
            budget="max 6 attempts",
            rollback_plan="git checkout -- .",
        )
        text = goal.render()
        assert "pytest green" in text
        assert "only core/loops" in text
        assert "pytest -x returncode 0" in text
        assert "git checkout -- ." in text


class TestHardenGoal:
    async def test_parses_llm_questionnaire(self):
        payload = json.dumps(
            {
                "goal": "pytest green on core/loops",
                "scope": "core/loops only",
                "verifier_description": "run pytest, exit code 0",
                "budget": "6 attempts",
                "rollback_plan": "revert the branch",
            }
        )
        hardened = await harden_goal("make tests pass", llm_service=_LLM(payload))
        assert hardened.goal == "pytest green on core/loops"
        assert hardened.scope == "core/loops only"

    async def test_malformed_response_fails_soft(self):
        hardened = await harden_goal("make tests pass", llm_service=_LLM("not json"))
        assert hardened.goal == "make tests pass"
        assert hardened.scope == ""

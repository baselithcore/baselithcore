"""Tests for DSPy-lite prompt compilation (bootstrap -> eval -> registry landing)."""

from __future__ import annotations

import pytest
from core.optimization.compile import CompiledPrompt, compile_prompt

from core.evaluation.prompt_eval import EvalCase, PromptEvaluator
from core.observability import audit as audit_module
from core.observability.audit import AuditEventType
from core.optimization.tune_gate import CANDIDATE_LABEL
from core.prompts.registry import PromptRegistry

pytestmark = [pytest.mark.unit]

BASE_PROMPT = "You are a terse quiz assistant."

# The chem answer is missing from the base answers, so its case fails the
# bootstrap; the demo-aware answers make every case pass once demos appear.
BASE_ANSWERS = {"Capital of France?": "Paris.", "What is 2+2?": "4"}
FULL_ANSWERS = {**BASE_ANSWERS, "Symbol for gold?": "Au"}


class _RecordingSink:
    def __init__(self) -> None:
        self.events: list = []

    async def write(self, event) -> None:
        self.events.append(event)


@pytest.fixture
def audit_sink(monkeypatch):
    sink = _RecordingSink()
    logger = audit_module.AuditLogger(sinks=[sink])
    monkeypatch.setattr(audit_module, "get_audit_logger", lambda: logger)
    return sink


@pytest.fixture
def registry(monkeypatch):
    reg = PromptRegistry()
    monkeypatch.setattr("core.prompts.registry.get_prompt_registry", lambda: reg)
    return reg


def _final_user(prompt: str) -> str:
    """Extract the eval case's user input (the *last* User: turn)."""
    return prompt.rsplit("User: ", 1)[1].removesuffix("\n\nAssistant:")


class ScriptedLLM:
    """Mock LLM whose answers depend on the demos block being in the prompt.

    ``base_answers`` drive un-demoed prompts; ``demo_answers`` drive prompts
    that carry the compiled "## Examples" block. Unknown inputs get a wrong
    answer so their cases fail.
    """

    def __init__(
        self,
        base_answers: dict[str, str],
        demo_answers: dict[str, str] | None = None,
    ) -> None:
        self.base_answers = dict(base_answers)
        self.demo_answers = (
            dict(demo_answers) if demo_answers is not None else dict(base_answers)
        )
        self.prompts: list[str] = []

    async def generate_response(self, prompt: str) -> str:
        self.prompts.append(prompt)
        answers = self.demo_answers if "## Examples" in prompt else self.base_answers
        return answers.get(_final_user(prompt), "no idea")


def _quiz_trainset() -> list[EvalCase]:
    return [
        EvalCase(
            name="capital",
            user_input="Capital of France?",
            expected_keywords=["Paris"],
        ),
        EvalCase(name="math", user_input="What is 2+2?", expected_keywords=["4"]),
        EvalCase(name="chem", user_input="Symbol for gold?", expected_keywords=["Au"]),
    ]


class TestCompileImproves:
    async def test_improved_lands_candidate_with_demos(self, registry, audit_sink):
        llm = ScriptedLLM(BASE_ANSWERS, FULL_ANSWERS)
        result = await compile_prompt(
            "quiz", BASE_PROMPT, _quiz_trainset(), llm_service=llm
        )
        assert isinstance(result, CompiledPrompt)
        assert result.improved is True
        assert result.baseline_pass_rate == pytest.approx(2 / 3)
        assert result.compiled_pass_rate == 1.0
        assert result.registered_version == "1"

        landed = registry.get("quiz", label=CANDIDATE_LABEL)
        assert landed.template == result.template
        assert landed.template.startswith(BASE_PROMPT)
        assert "## Examples" in landed.template
        assert "User: What is 2+2?\nAssistant: 4" in landed.template
        assert "User: Capital of France?\nAssistant: Paris." in landed.template

        applied = [
            e
            for e in audit_sink.events
            if e.event_type == AuditEventType.SELF_MODIFY_APPLY
        ]
        assert len(applied) == 1
        assert applied[0].resource == "quiz"
        assert applied[0].success is True
        assert applied[0].details["registered_version"] == "1"

    async def test_demos_harvested_only_from_passing_cases(self, registry, audit_sink):
        llm = ScriptedLLM(BASE_ANSWERS, FULL_ANSWERS)
        result = await compile_prompt(
            "quiz", BASE_PROMPT, _quiz_trainset(), llm_service=llm
        )
        # Shortest response first ("4" before "Paris."); failing chem excluded.
        assert result.demos == [
            ("What is 2+2?", "4"),
            ("Capital of France?", "Paris."),
        ]
        assert "Symbol for gold?" not in result.template

    async def test_register_false_skips_registration(self, registry, audit_sink):
        llm = ScriptedLLM(BASE_ANSWERS, FULL_ANSWERS)
        result = await compile_prompt(
            "quiz", BASE_PROMPT, _quiz_trainset(), llm_service=llm, register=False
        )
        assert result.improved is True
        assert result.registered_version is None
        assert registry.list_versions("quiz") == []
        applied = [
            e
            for e in audit_sink.events
            if e.event_type == AuditEventType.SELF_MODIFY_APPLY
        ]
        assert len(applied) == 1
        assert applied[0].details["registered_version"] is None


class TestDemoSelection:
    async def test_k_cap_and_shortest_preference_deterministic(
        self, registry, audit_sink
    ):
        # Scrambled trainset order: selection must sort, not rely on order.
        cases = [
            EvalCase(name="delta", user_input="d?", expected_keywords=["d"]),
            EvalCase(name="bravo", user_input="b?", expected_keywords=["b"]),
            EvalCase(name="alpha", user_input="a?", expected_keywords=["a"]),
            EvalCase(name="charlie", user_input="c?", expected_keywords=["c"]),
        ]
        answers = {"a?": "aa", "b?": "bb", "c?": "cccc", "d?": "dddddddd"}
        first = await compile_prompt(
            "caps", BASE_PROMPT, cases, llm_service=ScriptedLLM(answers), k_demos=2
        )
        # len tie between alpha/bravo broken by case name; charlie/delta capped.
        assert first.demos == [("a?", "aa"), ("b?", "bb")]
        second = await compile_prompt(
            "caps", BASE_PROMPT, cases, llm_service=ScriptedLLM(answers), k_demos=2
        )
        assert second.demos == first.demos
        assert second.template == first.template

    async def test_k_demos_must_be_positive(self):
        with pytest.raises(ValueError):
            await compile_prompt(
                "quiz", BASE_PROMPT, [], llm_service=ScriptedLLM({}), k_demos=0
            )


class TestCompileDoesNotImprove:
    async def test_not_improved_nothing_registered_reject_audited(
        self, registry, audit_sink
    ):
        # Demo-blind LLM: candidate scores exactly the baseline -> rejected.
        llm = ScriptedLLM(BASE_ANSWERS)
        result = await compile_prompt(
            "quiz", BASE_PROMPT, _quiz_trainset(), llm_service=llm
        )
        assert result.improved is False
        assert result.registered_version is None
        assert registry.list_versions("quiz") == []
        rejected = [
            e
            for e in audit_sink.events
            if e.event_type == AuditEventType.SELF_MODIFY_REJECT
        ]
        assert len(rejected) == 1
        assert rejected[0].resource == "quiz"
        assert rejected[0].success is False

    async def test_zero_passing_bootstrap_returns_early(self, registry, audit_sink):
        llm = ScriptedLLM({})  # every case fails the bootstrap
        trainset = _quiz_trainset()
        result = await compile_prompt("quiz", BASE_PROMPT, trainset, llm_service=llm)
        assert result.improved is False
        assert result.demos == []
        assert result.registered_version is None
        assert result.template == BASE_PROMPT
        assert registry.list_versions("quiz") == []
        # Bootstrap only: no baseline/candidate evaluation calls were made.
        assert len(llm.prompts) == len(trainset)
        assert audit_sink.events == []


class TestValsetAndFactory:
    async def test_valset_used_when_provided(self, registry, audit_sink):
        trainset = [EvalCase(name="train", user_input="t?", expected_keywords=["T"])]
        valset = [EvalCase(name="val", user_input="v?", expected_keywords=["V"])]
        llm = ScriptedLLM({"t?": "T"}, {"t?": "T", "v?": "V"})
        result = await compile_prompt(
            "val-quiz", BASE_PROMPT, trainset, llm_service=llm, valset=valset
        )
        # Pass rates come from the valset (trainset baseline would be 1.0).
        assert result.baseline_pass_rate == 0.0
        assert result.compiled_pass_rate == 1.0
        assert result.improved is True
        assert [_final_user(p) for p in llm.prompts] == ["t?", "v?", "v?"]
        landed = registry.get("val-quiz", label=CANDIDATE_LABEL)
        assert landed.template == result.template

    async def test_evaluator_factory_injected_and_bootstrap_reused(
        self, registry, audit_sink
    ):
        seen: list[str] = []

        def factory(system_prompt: str, llm_service) -> PromptEvaluator:
            seen.append(system_prompt)
            return PromptEvaluator(system_prompt, llm_service=llm_service)

        llm = ScriptedLLM(BASE_ANSWERS, FULL_ANSWERS)
        result = await compile_prompt(
            "quiz",
            BASE_PROMPT,
            _quiz_trainset(),
            llm_service=llm,
            evaluator_factory=factory,
        )
        assert result.improved is True
        # Without a valset the bootstrap run doubles as the baseline:
        # exactly two evaluators — base prompt, then the compiled candidate.
        assert seen == [BASE_PROMPT, result.template]

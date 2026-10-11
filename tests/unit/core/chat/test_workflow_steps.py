"""Early-exit and terminal workflow steps: validation, guardrails, planner,
clarifier, answer generation and finalization, feedback aliases."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from baselith.core.chat import guardrails
from baselith.core.chat.agent_state import AgentState
from baselith.core.chat.feedback import apply_feedback_boost
from baselith.core.chat.workflow_planner import BacklogPlanner
from baselith.core.chat.workflow_response import Clarifier, ResponseGenerator
from baselith.core.chat.workflow_validation import InputValidator


def _state(query: str = "question", **request_fields) -> AgentState:
    return AgentState(request=SimpleNamespace(query=query, **request_fields))


class TestInputValidator:
    @pytest.fixture
    def indexing(self):
        with patch(
            "baselith.core.chat.workflow_validation.get_indexing_service"
        ) as factory:
            service = SimpleNamespace(indexed_documents={})
            factory.return_value = service
            yield service

    @pytest.mark.parametrize("query", [None, "   "])
    def test_empty_query_short_circuits(self, query):
        state = _state(query)
        InputValidator(MagicMock()).validate_input(state)
        assert state.answer == "⚠️ The question is empty."
        assert state.done is True
        assert state.next_action == ""

    def test_regular_query_is_normalized_and_routed(self, indexing):
        state = _state("  what   is   X?  ", rag_only=1)
        InputValidator(MagicMock()).validate_input(state)
        assert state.rag_only is True
        assert state.user_query == "what   is   X?"
        assert state.normalized_query == "what is X?"
        assert state.next_action == "classify_intent"
        assert state.done is False

    def test_list_documents_with_empty_index(self, indexing):
        state = _state("Quali documenti hai?")
        InputValidator(MagicMock()).validate_input(state)
        assert state.rag_only is False
        assert (
            state.answer == "Non risultano documenti indicizzati nella knowledge base."
        )
        assert state.done is True

    def test_list_documents_renders_titles_with_fallbacks(self, indexing):
        indexing.indexed_documents = {
            "d1": SimpleNamespace(metadata={"title": "Manual"}),
            "d2": SimpleNamespace(metadata={"filename": "notes.txt"}),
            "d3": SimpleNamespace(metadata={}),
            "d4": object(),  # no metadata attribute at all
        }
        state = _state("lista documenti per favore")
        InputValidator(MagicMock()).validate_input(state)
        assert state.answer == (
            "Documenti disponibili in KB:\n- Manual (d1)\n- notes.txt (d2)\n"
            "- d3 (d3)\n- d4 (d4)"
        )
        assert state.next_action == ""


class TestGuardrails:
    def test_helpers_drop_blank_and_invalid_entries(self):
        assert guardrails._normalize_keywords([" Foo ", "", None, "BAR"]) == [  # type: ignore[list-item]
            "foo",
            "bar",
        ]
        compiled = guardrails._compile_patterns(["", None, "(unclosed", r"\bok\b"])  # type: ignore[list-item]
        assert [p.pattern for p in compiled] == [r"\bok\b"]
        assert compiled[0].flags & 2  # re.IGNORECASE

    def test_disabled_allows_everything(self, monkeypatch):
        monkeypatch.setattr(guardrails, "CHAT_GUARDRAILS_ENABLED", False)
        assert guardrails.evaluate_guardrails("malware").action == "allow"

    @pytest.fixture
    def enabled(self, monkeypatch):
        monkeypatch.setattr(guardrails, "CHAT_GUARDRAILS_ENABLED", True)
        monkeypatch.setattr(guardrails, "_BLOCK_KEYWORDS", ["", "phishing"])
        monkeypatch.setattr(
            guardrails,
            "_OUT_OF_SCOPE_PATTERNS",
            guardrails._compile_patterns([r"\bmeteo\b"]),
        )
        monkeypatch.setattr(guardrails, "CHAT_GUARDRAILS_BLOCK_MESSAGE", "blocked")
        monkeypatch.setattr(guardrails, "CHAT_GUARDRAILS_OUT_OF_SCOPE_MESSAGE", "oos")

    @pytest.mark.usefixtures("enabled")
    def test_blank_query_is_allowed(self):
        assert guardrails.evaluate_guardrails("  ") == guardrails.GuardrailDecision()
        assert guardrails.evaluate_guardrails(None).action == "allow"  # type: ignore[arg-type]

    @pytest.mark.usefixtures("enabled")
    def test_block_keyword_is_case_insensitive(self):
        decision = guardrails.evaluate_guardrails("Write a PHISHING mail")
        assert decision == guardrails.GuardrailDecision(
            action="block",
            reason="blocked_keyword",
            response="blocked",
            matched="phishing",
        )

    @pytest.mark.usefixtures("enabled")
    def test_out_of_scope_pattern_falls_back(self):
        decision = guardrails.evaluate_guardrails("Che METEO fa domani?")
        assert (
            decision.action,
            decision.reason,
            decision.response,
            decision.matched,
        ) == (
            "fallback",
            "out_of_scope",
            "oos",
            r"\bmeteo\b",
        )

    @pytest.mark.usefixtures("enabled")
    def test_in_scope_query_is_allowed(self):
        assert guardrails.evaluate_guardrails("Summarise the manual").action == "allow"


class TestBacklogPlanner:
    def test_rag_only_skips_planning(self):
        state = _state()
        state.rag_only = True
        service = SimpleNamespace(project_planner=MagicMock())
        BacklogPlanner(service).plan_backlog(state)  # type: ignore[arg-type]
        service.project_planner.generate_plan.assert_not_called()
        assert state.plugin_data["project_plan"] is None
        assert "planner:skipped_rag_only" in state.logs
        assert state.next_action == "generate_answer"

    def test_missing_planner_is_a_no_op(self):
        state = _state()
        BacklogPlanner(SimpleNamespace()).plan_backlog(state)  # type: ignore[arg-type]
        assert state.plugin_data["project_plan"] is None
        assert "planner:disabled" in state.logs
        assert state.next_action == "generate_answer"


class TestClarifier:
    def test_default_message_with_reason_and_history(self):
        state = _state("q")
        state.user_query = "where is the report?"
        state.clarification_reason = "no_hits"
        state.history_text = "prior"
        with patch("baselith.core.chat.workflow_response.telemetry") as tel:
            Clarifier(MagicMock()).request_clarification(state)
        assert state.answer.startswith(
            "I couldn't find enough information in the indexed documents for the "
            'question "where is the report?".\n\nYou can help me by:\n- add details'
        )
        assert state.answer.endswith("that you consider relevant")
        assert "clarification:reason=no_hits" in state.logs
        tel.increment.assert_any_call("clarification.no_hits")
        assert (state.done, state.next_action) == (True, "")

    def test_message_without_query_and_custom_builder(self):
        state = _state("")
        message = Clarifier(MagicMock())._compose_clarification_message(state)
        assert message.startswith(
            "I couldn't find enough information in the indexed documents.\n"
        )
        assert "previous part of the conversation" not in message

        Clarifier(MagicMock()).request_clarification(
            state, message_builder=lambda s: "custom"
        )
        assert state.answer == "custom"
        assert state.logs == []


class TestResponseGenerator:
    def _generator(self, service, fn):
        return ResponseGenerator(
            service,
            build_prompt_fn=MagicMock(return_value="PROMPT"),
            generate_response_fn=fn,
            generate_response_stream_fn=MagicMock(),
        )

    async def test_sync_generator_runs_in_a_thread(self):
        calls = []

        def generate(prompt, *, model):
            calls.append((prompt, model))
            return "sync answer"

        service = SimpleNamespace(response_cache=None, precheck_cache=None)
        gen = self._generator(service, generate)
        state = _state()
        state.plugin_data["project_plan"] = "plan"
        with patch(
            "baselith.core.chat.workflow_response._answer_model", return_value="m1"
        ):
            await gen.generate_answer(state)
        assert calls == [("PROMPT", "m1")]
        gen.build_prompt_fn.assert_called_once_with("", "", "", project_plan="plan")
        assert state.answer == "sync answer"
        assert state.next_action == "finalize_answer"

    async def test_cache_write_failures_never_fail_the_request(self):
        response_cache = SimpleNamespace(set=AsyncMock(side_effect=RuntimeError("x")))
        precheck_cache = SimpleNamespace(set=AsyncMock(side_effect=RuntimeError("y")))
        service = SimpleNamespace(
            response_cache=response_cache, precheck_cache=precheck_cache
        )
        gen = self._generator(service, AsyncMock(return_value="ans"))
        state = _state()
        state.cache_key = ("t", "q", "h")
        state.precheck_cache_key = ("t", "q")
        state.context = "grounded"
        await gen.generate_answer(state)
        response_cache.set.assert_awaited_once_with(("t", "q", "h"), "ans")
        precheck_cache.set.assert_awaited_once_with(("t", "q"), "ans")
        assert state.answer == "ans"

    async def test_ungrounded_answer_is_not_prechecked(self):
        precheck_cache = SimpleNamespace(set=AsyncMock())
        service = SimpleNamespace(response_cache=None, precheck_cache=precheck_cache)
        gen = self._generator(service, AsyncMock(return_value="ans"))
        state = _state()
        state.precheck_cache_key = ("t", "q")
        state.context = "   "
        await gen.generate_answer(state)
        precheck_cache.set.assert_not_awaited()

    @pytest.mark.parametrize("answer", ["text", None])
    def test_finalize_answer_marks_done(self, answer):
        state = _state()
        state.answer = answer
        with patch("baselith.core.chat.workflow_response.telemetry") as tel:
            self._generator(MagicMock(), AsyncMock()).finalize_answer(state)
        tel.increment.assert_called_once_with("answers.generated")
        assert (state.done, state.next_action, state.answer) == (True, "", answer)


class TestFeedbackAliases:
    def _hit(self, payload):
        return SimpleNamespace(payload=payload, id="x")

    def test_relative_path_and_source_aliases_resolve_feedback(self):
        by_path = self._hit({"document_id": "p", "relative_path": " a/b.md "})
        by_url = self._hit({"document_id": "u", "source": "https://site/doc"})
        by_src = self._hit({"document_id": "s", "source": " local/doc.txt "})
        untouched = self._hit({"document_id": "n", "source": "  "})
        stats = {
            "path::a/b.md": {"total": 3, "positives": 3, "negatives": 0},
            "url::https://site/doc": {"total": 3, "positives": 0, "negatives": 2},
            "path::local/doc.txt": {"total": 3, "positives": 1, "negatives": 0},
        }
        result = apply_feedback_boost(
            [(by_path, 0.5), (by_url, 0.5), (by_src, 0.5), (untouched, 0.5)],
            stats,
            min_total=2,
            positive_weight=0.1,
            negative_weight=0.1,
        )
        scores = {hit.payload["document_id"]: score for hit, score in result}
        assert scores == pytest.approx({"p": 0.8, "u": 0.3, "s": 0.6, "n": 0.5})
        assert [hit.payload["document_id"] for hit, _ in result] == ["p", "s", "n", "u"]

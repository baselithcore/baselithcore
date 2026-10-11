"""Answer post-processing helpers: response.py, streaming.py, prompt_engine.py."""

from types import SimpleNamespace

import pytest

from baselith.core.chat.prompt_engine import (
    FewShotExample,
    PromptEngine,
    make_context_block,
    make_research_prompt,
)
from baselith.core.chat.response import (
    append_sources,
    ensure_string_answer,
    strip_sources_section,
)
from baselith.core.chat.streaming import (
    build_cached_stream,
    build_fallback_stream,
    stream_answer,
)
from baselith.core.chat.workflows import (
    ClarifierProtocol,
    ResponseGeneratorProtocol,
    RetrieverProtocol,
    ValidatorProtocol,
    WorkflowStep,
)


class TestAppendSources:
    def test_no_renderable_sources_returns_answer_unchanged(self):
        sources = [{"title": "no link"}, "not-a-dict", {"url": "ftp://x"}]
        assert append_sources("Answer.", sources) == "Answer."  # type: ignore[list-item]

    def test_url_sources_are_deduplicated_and_carry_metrics(self):
        sources = [
            {"url": " https://a ", "title": " A ", "context_ratio": 0.5, "score_avg": 0.81},
            {"url": "https://a", "title": "dup"},
            {"url": "https://b", "title": "  ", "score": 0.3, "context_ratio": 0},
        ]  # fmt: skip
        out = append_sources("Answer.  \n", sources)
        assert out == (
            "Answer.\n\nFonti:\n"
            "- [Fonte](https://a) - A (copertura 50%, score 0.81)\n"
            "- [Fonte](https://b) - https://b (score 0.30)"
        )

    def test_path_sources_are_escaped_and_deduplicated(self):
        sources = [
            {"path": " a<b>&c.md ", "title": "Doc"},
            {"path": "a<b>&c.md", "title": "dup"},
            {"path": "plain.md", "title": 3},
        ]
        out = append_sources("", sources)
        assert out == (
            "Fonti:\n"
            "- File: <code>a&lt;b&gt;&amp;c.md</code> - Doc\n"
            "- File: <code>plain.md</code> - plain.md"
        )

    def test_score_avg_preferred_over_score(self):
        out = append_sources(
            "x", [{"url": "https://a", "score_avg": 0.1, "score": 0.9}]
        )
        assert out.endswith("(score 0.10)")


class TestStripAndEnsure:
    @pytest.mark.parametrize(
        ("answer", "expected"),
        [
            ("Body text.\n\nFonti:\n- a\n- b", "Body text."),
            ("Body\nSources :\n- x", "Body"),
            ("Sources:\n- only sources", ""),
            ("No section here: fonti inline", "No section here: fonti inline"),
        ],
    )
    def test_strip_sources_section(self, answer, expected):
        assert strip_sources_section(answer) == expected

    def test_ensure_string_answer(self):
        assert ensure_string_answer("x") == "x"
        assert ensure_string_answer(12) == "12"
        assert ensure_string_answer(None) == "None"


class TestStreaming:
    def test_cached_and_fallback_streams_yield_the_answer_once(self):
        assert list(build_cached_stream("cached")) == ["cached"]
        assert list(build_fallback_stream("fallback")) == ["fallback"]

    def test_stream_answer_skips_empty_chunks_and_emits_suffix(self):
        seen: list[tuple[str, str]] = []

        def stream_fn(prompt: str):
            assert prompt == "P"
            yield from ["  Hello", "", " world  "]

        chunks = list(
            stream_answer(
                "P",
                stream_fn=stream_fn,
                finalize_fn=lambda text: text + "\n\nFonti:\n- x",
                on_finalize=lambda final, raw: seen.append((final, raw)),
            )
        )
        assert chunks == ["  Hello", " world  ", "\n\nFonti:\n- x"]
        assert seen == [("Hello world\n\nFonti:\n- x", "Hello world")]

    def test_stream_answer_without_suffix_or_callback(self):
        chunks = list(
            stream_answer(
                "P",
                stream_fn=lambda _p: iter(["abc"]),
                finalize_fn=lambda text: text[:1],
            )
        )
        assert chunks == ["abc"]

    def test_stream_is_lazy(self):
        calls: list[str] = []

        def stream_fn(prompt: str):
            calls.append(prompt)
            return iter(["x"])

        gen = stream_answer("P", stream_fn=stream_fn, finalize_fn=str)
        assert calls == []
        assert next(gen) == "x"
        assert calls == ["P"]


class TestPromptEngine:
    def test_render_orders_layers_and_substitutes_without_format(self):
        engine = PromptEngine(
            identity="  You are {name}.  ",
            instructions="Rules for {name}.",
            output_constraints='Respond in JSON: {"answer": "..."}',
        )
        engine.with_example(FewShotExample("hi", "hello", label="greet"))
        engine.with_examples([FewShotExample("q", "a")])

        out = engine.render(context="  ctx for {name}  ", name="Atlas")
        assert out == (
            "You are Atlas.\n\nRules for Atlas.\n\n## Examples\n\n"
            "## Example (greet)\nUser: hi\nAgent: hello\n\n"
            "## Example\nUser: q\nAgent: a\n\n"
            "## Current Context\n\nctx for Atlas\n\n"
            'Respond in JSON: {"answer": "..."}'
        )

    def test_render_minimal_engine_omits_optional_layers(self):
        engine = PromptEngine(identity="I", instructions="R")
        assert engine.render(context="   ") == "I\n\nR"
        assert engine.changelog == ["v1.0 - Initial version"]

    def test_build_layers_folds_examples_into_instructions(self):
        engine = PromptEngine(identity="I", instructions="R", output_constraints="O")
        assert engine.build_layers("c").instructions == "R"
        engine.with_example(FewShotExample("u", "a"))
        layers = engine.build_layers("c")
        assert layers.instructions == "R\n\n## Example\nUser: u\nAgent: a"
        assert (layers.identity, layers.context, layers.output_constraints) == (
            "I",
            "c",
            "O",
        )

    def test_with_library_uses_first_tag_or_task_type_as_label(self):
        examples = [
            SimpleNamespace(input="i1", output="o1", tags=["search"]),
            SimpleNamespace(input="i2", output="o2", tags=[]),
        ]

        class _Library:
            def __init__(self) -> None:
                self.calls: list[tuple] = []

            def select(self, task_type, *, limit, tags):
                self.calls.append((task_type, limit, tags))
                return examples

        library = _Library()
        engine = PromptEngine(identity="I", instructions="R")
        assert engine.with_library(library, "qa", limit=2, tags=["x"]) is engine
        assert library.calls == [("qa", 2, ["x"])]
        layers = engine.build_layers()
        assert "## Example (search)\nUser: i1" in layers.instructions
        assert "## Example (qa)\nUser: i2" in layers.instructions

    def test_versioning(self):
        engine = PromptEngine(
            identity="I", instructions="R", version="2.1", changelog=["v2.0 - a"]
        )
        engine.add_changelog_entry("v2.1 - b")
        assert engine.version_info() == (
            "Prompt version: 2.1\n\nChangelog:\n  v2.0 - a\n  v2.1 - b"
        )

    def test_research_prompt_factory(self):
        engine = make_research_prompt(agent_name="Nova", max_tool_calls=3, version="3")
        rendered = engine.render()
        assert rendered.startswith("You are Nova, a senior research analyst.")
        assert "If you reach 3 tool calls" in rendered
        assert rendered.endswith("- <URL 2>")
        assert engine.changelog == ["v3 - Initial research agent prompt"]

    def test_context_block(self):
        block = make_context_block(
            user_name="Ann",
            timezone="Europe/Rome",
            session_summary="  building  ",
            interaction_count=4,
            current_date="2026-01-02",
        )
        assert block == (
            "User: Ann\nTimezone: Europe/Rome\nDate: 2026-01-02\n"
            "Interactions this session: 4\n\nSession summary: building"
        )

    def test_context_block_defaults(self):
        block = make_context_block()
        assert "User: Anonymous" in block
        assert block.endswith("Session summary: This is the first interaction.")
        date_line = block.splitlines()[2]
        assert len(date_line.removeprefix("Date: ")) == len("2026-01-01")


class TestWorkflowProtocols:
    def test_protocol_stub_bodies_return_none(self):
        state = object()
        assert ValidatorProtocol.validate_input(None, state) is None  # type: ignore[arg-type]
        assert RetrieverProtocol.retrieve(None, state) is None  # type: ignore[arg-type]
        assert ResponseGeneratorProtocol.generate_answer(None, state) is None  # type: ignore[arg-type]
        assert ResponseGeneratorProtocol.generate_answer_stream(None, state) is None  # type: ignore[arg-type]
        assert ResponseGeneratorProtocol.finalize_answer(None, state) is None  # type: ignore[arg-type]
        assert ClarifierProtocol.request_clarification(None, state) is None  # type: ignore[arg-type]

    def test_workflow_step_is_abstract(self):
        with pytest.raises(TypeError):
            WorkflowStep()  # type: ignore[abstract]

        class _Step(WorkflowStep):
            @property
            def name(self) -> str:
                return WorkflowStep.name.fget(self) or "step"  # type: ignore[attr-defined]

            def execute(self, state) -> None:
                WorkflowStep.execute(self, state)
                state.append("ran")

        step = _Step()
        trace: list[str] = []
        step.execute(trace)
        assert (step.name, trace) == ("step", ["ran"])

"""LLM, CLASSIFIER and RETRIEVE nodes with deterministic stand-ins."""

import itertools
from types import SimpleNamespace
from typing import Any

import pytest

from baselith.core.workflows.builder import (
    NodeType,
    WorkflowDefinition,
    WorkflowEdge,
    WorkflowNode,
)
from baselith.core.workflows.executor import ExecutionStatus, WorkflowExecutor
from baselith.core.workflows.nodes_ai import pick_class_edge

pytestmark = [pytest.mark.unit]


class FakeLLM:
    """Records calls and answers from a queue."""

    def __init__(self, *answers: str, truncated: bool = False) -> None:
        self.answers = list(answers)
        self.calls: list[dict[str, Any]] = []
        self.truncated = truncated

    async def generate_response(self, prompt: str, **kwargs: Any) -> str:
        self.calls.append({"kind": "text", "prompt": prompt, **kwargs})
        return self.answers.pop(0)

    async def generate(self, prompt: str, **kwargs: Any) -> Any:
        self.calls.append({"kind": "structured", "prompt": prompt, **kwargs})
        return SimpleNamespace(text=self.answers.pop(0), truncated=self.truncated)


def _wf(*nodes: WorkflowNode, edges=None) -> WorkflowDefinition:
    wf = WorkflowDefinition(name="t")
    start = WorkflowNode(id="start", type=NodeType.START, label="s")
    end = WorkflowNode(id="end", type=NodeType.END, label="e")
    for n in (start, *nodes, end):
        wf.add_node(n)
    if edges is None:
        order = [start, *nodes, end]
        edges = [(a.id, b.id, None) for a, b in itertools.pairwise(order)]
    for i, (a, b, label) in enumerate(edges):
        wf.add_edge(
            WorkflowEdge(id=f"e{i}", source_id=a, target_id=b, condition_label=label)
        )
    return wf


def _llm_node(**config) -> WorkflowNode:
    return WorkflowNode(id="llm", type=NodeType.LLM, label="llm", config=config)


class TestLlmNode:
    async def test_templated_prompt_and_system(self):
        llm = FakeLLM("done")
        wf = _wf(
            _llm_node(
                prompt="Summarise: {{ input }}", system="Be brief about {{ input }}"
            )
        )
        result = await WorkflowExecutor(services={"llm": llm}).execute(
            wf, initial_input="cats"
        )
        assert result.output == "done"
        assert llm.calls[0]["prompt"] == "Summarise: cats"
        assert llm.calls[0]["system_prompt"] == "Be brief about cats"
        assert llm.calls[0]["json"] is False

    async def test_json_mode_parses_and_strips_fences(self):
        llm = FakeLLM('```json\n{"a": 1}\n```')
        wf = _wf(_llm_node(prompt="p", json=True))
        result = await WorkflowExecutor(services={"llm": llm}).execute(wf)
        assert result.output == {"a": 1}

    async def test_schema_uses_structured_path_and_validates(self):
        schema = {
            "type": "object",
            "properties": {"n": {"type": "integer"}},
            "required": ["n"],
        }
        llm = FakeLLM('{"n": 3}')
        wf = _wf(_llm_node(prompt="p", schema=schema))
        result = await WorkflowExecutor(services={"llm": llm}).execute(wf)
        assert result.output == {"n": 3}
        assert llm.calls[0]["kind"] == "structured"
        assert llm.calls[0]["response_format"].schema == schema

    async def test_schema_violation_fails_the_node(self):
        schema = {
            "type": "object",
            "properties": {"n": {"type": "integer"}},
            "required": ["n"],
        }
        wf = _wf(_llm_node(prompt="p", schema=schema))
        result = await WorkflowExecutor(
            services={"llm": FakeLLM('{"n": "x"}')}
        ).execute(wf)
        assert result.status is ExecutionStatus.FAILED
        assert "violates its schema" in (result.error or "")

    async def test_invalid_json_fails(self):
        wf = _wf(_llm_node(prompt="p", json=True))
        result = await WorkflowExecutor(services={"llm": FakeLLM("not json")}).execute(
            wf
        )
        assert result.status is ExecutionStatus.FAILED
        assert "valid JSON" in (result.error or "")

    async def test_truncated_structured_answer_is_rejected(self):
        wf = _wf(_llm_node(prompt="p", schema={"type": "object"}))
        llm = FakeLLM('{"a":', truncated=True)
        result = await WorkflowExecutor(services={"llm": llm}).execute(wf)
        assert result.status is ExecutionStatus.FAILED
        assert "cut short" in (result.error or "")

    async def test_missing_prompt_fails(self):
        result = await WorkflowExecutor(services={"llm": FakeLLM("x")}).execute(
            _wf(_llm_node())
        )
        assert result.status is ExecutionStatus.FAILED

    async def test_retries_use_the_node_policy(self):
        class Flaky(FakeLLM):
            async def generate_response(self, prompt, **kwargs):
                self.calls.append({})
                if len(self.calls) == 1:
                    raise ConnectionError("blip")
                return "ok"

        node = _llm_node(prompt="p")
        node.retries = 1
        node.retry_backoff = 0
        llm = Flaky()
        result = await WorkflowExecutor(services={"llm": llm}).execute(_wf(node))
        assert result.output == "ok" and len(llm.calls) == 2


def _classifier_wf(**config) -> WorkflowDefinition:
    cls = WorkflowNode(id="cls", type=NodeType.CLASSIFIER, label="c", config=config)
    a = WorkflowNode(
        id="a", type=NodeType.TEMPLATE, label="a", config={"template": "A:{{ input }}"}
    )
    b = WorkflowNode(
        id="b", type=NodeType.TEMPLATE, label="b", config={"template": "B:{{ input }}"}
    )
    wf = _wf(
        cls,
        a,
        b,
        edges=[
            ("start", "cls", None),
            ("cls", "a", "billing"),
            ("cls", "b", "support"),
            ("a", "end", None),
            ("b", "end", None),
        ],
    )
    return wf


CATEGORIES = [
    {
        "label": "billing",
        "description": "money questions",
        "keywords": ["invoice", "refund"],
    },
    {"label": "support", "description": "technical problems", "keywords": ["crash"]},
]


class TestClassifierNode:
    async def test_single_keyword_hit_skips_the_model(self):
        llm = FakeLLM()
        wf = _classifier_wf(categories=CATEGORIES, text="{{ input }}")
        result = await WorkflowExecutor(services={"llm": llm}).execute(
            wf, initial_input="Where is my REFUND?"
        )
        assert result.output == "A:Where is my REFUND?"
        assert llm.calls == []

    async def test_keywords_match_whole_words_only(self):
        llm = FakeLLM('{"label": "support"}')
        wf = _classifier_wf(categories=CATEGORIES, text="{{ input }}")
        result = await WorkflowExecutor(services={"llm": llm}).execute(
            wf, initial_input="the refunded crashes"
        )
        assert llm.calls, "no whole-word keyword hit must fall through to the model"
        assert result.output.startswith("B:")

    async def test_no_hit_asks_the_model_with_an_enum_schema(self):
        llm = FakeLLM('{"label": "support"}')
        wf = _classifier_wf(categories=CATEGORIES, text="{{ input }}")
        result = await WorkflowExecutor(services={"llm": llm}).execute(
            wf, initial_input="please help"
        )
        assert result.output == "B:please help"
        schema = llm.calls[0]["response_format"].schema
        assert schema["properties"]["label"]["enum"] == ["billing", "support"]
        assert "please help" in llm.calls[0]["prompt"]

    async def test_model_answer_outside_the_labels_is_not_trusted(self):
        # An injected text steering the model to a label that does not exist
        # must not pick an edge: the default (or failure) applies instead.
        llm = FakeLLM('{"label": "admin"}')
        wf = _classifier_wf(
            categories=CATEGORIES, default="support", text="{{ input }}"
        )
        result = await WorkflowExecutor(services={"llm": llm}).execute(
            wf, initial_input="x"
        )
        assert result.output == "B:x"

    async def test_unmatched_without_default_fails_closed(self):
        llm = FakeLLM('{"label": "admin"}')
        wf = _classifier_wf(categories=CATEGORIES, text="{{ input }}")
        result = await WorkflowExecutor(services={"llm": llm}).execute(
            wf, initial_input="x"
        )
        assert result.status is ExecutionStatus.FAILED
        assert "no class matched" in (result.error or "")

    async def test_keyword_only_mode_never_calls_the_model(self):
        llm = FakeLLM()
        wf = _classifier_wf(
            categories=CATEGORIES, default="support", use_llm=False, text="{{ input }}"
        )
        result = await WorkflowExecutor(services={"llm": llm}).execute(
            wf, initial_input="zzz"
        )
        assert result.output == "B:zzz" and llm.calls == []

    async def test_class_without_an_edge_is_rejected_before_the_run(self):
        wf = _classifier_wf(
            categories=CATEGORIES + [{"label": "other"}], text="{{ input }}"
        )
        result = await WorkflowExecutor(services={"llm": FakeLLM()}).execute(
            wf, initial_input="q"
        )
        assert result.status is ExecutionStatus.FAILED
        assert "no edge for class 'other'" in (result.error or "")

    def test_pick_class_edge_never_falls_through_to_an_arbitrary_branch(self):
        edges = [
            WorkflowEdge(id="1", source_id="c", target_id="a", condition_label="a"),
            WorkflowEdge(id="2", source_id="c", target_id="b", condition_label="b"),
        ]
        assert pick_class_edge("b", edges).target_id == "b"
        with pytest.raises(RuntimeError, match="No outgoing edge"):
            pick_class_edge("zzz", edges)

    async def test_default_edge_catches_unlisted_classes(self):
        cls = WorkflowNode(
            id="cls",
            type=NodeType.CLASSIFIER,
            label="c",
            config={
                "categories": CATEGORIES + [{"label": "other"}],
                "text": "{{ input }}",
            },
        )
        a = WorkflowNode(
            id="a", type=NodeType.TEMPLATE, label="a", config={"template": "caught"}
        )
        wf = _wf(
            cls,
            a,
            edges=[
                ("start", "cls", None),
                ("cls", "a", "default"),
                ("a", "end", None),
            ],
        )
        llm = FakeLLM('{"label": "other"}')
        result = await WorkflowExecutor(services={"llm": llm}).execute(
            wf, initial_input="q"
        )
        assert result.output == "caught"


class TestRetrieveNode:
    async def test_calls_the_retriever_with_the_rendered_query(self):
        seen = {}

        async def retriever(query, *, top_k, collection):
            seen.update(query=query, top_k=top_k, collection=collection)
            return [
                {"id": "1", "content": "alpha", "score": 0.9, "metadata": {}},
                {"id": "2", "content": "beta", "score": 0.2, "metadata": {}},
            ]

        node = WorkflowNode(
            id="r",
            type=NodeType.RETRIEVE,
            label="r",
            config={
                "query": "about {{ input }}",
                "top_k": 3,
                "collection": "docs",
                "min_score": 0.5,
            },
        )
        result = await WorkflowExecutor(services={"retriever": retriever}).execute(
            _wf(node), initial_input="cats"
        )
        assert seen == {"query": "about cats", "top_k": 3, "collection": "docs"}
        assert [h["id"] for h in result.output] == ["1"]

    async def test_top_k_is_clamped(self):
        seen = {}

        async def retriever(query, *, top_k, collection):
            seen["k"] = top_k
            return []

        node = WorkflowNode(
            id="r", type=NodeType.RETRIEVE, label="r", config={"top_k": 9999}
        )
        result = await WorkflowExecutor(services={"retriever": retriever}).execute(
            _wf(node), initial_input="q"
        )
        assert seen["k"] == 50 and result.output == []

    async def test_empty_query_fails(self):
        async def retriever(query, *, top_k, collection):  # pragma: no cover
            return []

        node = WorkflowNode(id="r", type=NodeType.RETRIEVE, label="r", config={})
        result = await WorkflowExecutor(services={"retriever": retriever}).execute(
            _wf(node), initial_input="   "
        )
        assert result.status is ExecutionStatus.FAILED

    async def test_retriever_failure_is_not_swallowed(self):
        async def retriever(query, *, top_k, collection):
            raise ConnectionError("vector store down")

        node = WorkflowNode(id="r", type=NodeType.RETRIEVE, label="r", config={})
        result = await WorkflowExecutor(services={"retriever": retriever}).execute(
            _wf(node), initial_input="q"
        )
        assert result.status is ExecutionStatus.FAILED
        assert "vector store down" in (result.error or "")

    async def test_pipeline_retrieve_then_llm(self):
        async def retriever(query, *, top_k, collection):
            return [{"id": "1", "content": "Paris", "score": 1.0, "metadata": {}}]

        retrieve = WorkflowNode(id="r", type=NodeType.RETRIEVE, label="r", config={})
        llm_node = _llm_node(
            prompt="Context:\n{{ nodes.r | pluck(content) | join('\n') }}\nQ: {{ input }}"
        )
        llm = FakeLLM("Paris.")
        result = await WorkflowExecutor(
            services={"retriever": retriever, "llm": llm}
        ).execute(_wf(retrieve, llm_node), initial_input="capital of France?")
        assert result.output == "Paris."
        assert llm.calls[0]["prompt"] == "Context:\nParis\nQ: capital of France?"

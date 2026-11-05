"""YAML round trip, hardened loading and static node validation."""

import pytest

from baselith.core.workflows.builder import (
    NodeType,
    WorkflowBuilder,
    WorkflowDefinition,
    WorkflowEdge,
    WorkflowNode,
)
from baselith.core.workflows.serialization import (
    MAX_DEFINITION_BYTES,
    DefinitionError,
    dump_yaml,
    load_definition,
    load_definition_file,
    write_definition_file,
)

pytestmark = [pytest.mark.unit]


def _sample() -> WorkflowDefinition:
    wf = (
        WorkflowBuilder("rag")
        .start()
        .retrieve("find", query="{{ input }}", top_k=3)
        .llm("answer", "Context: {{ output }}\nQ: {{ input }}", temperature=0.2)
        .end()
        .build()
    )
    wf.webhook_trigger = True
    wf.schedule = "*/15 * * * *"
    return wf


class TestYaml:
    def test_round_trip_preserves_the_graph(self):
        wf = _sample()
        again = WorkflowDefinition.from_yaml(wf.to_yaml())
        assert again.to_dict()["nodes"] == wf.to_dict()["nodes"]
        assert again.to_dict()["edges"] == wf.to_dict()["edges"]
        assert again.webhook_trigger is True
        assert again.schedule == "*/15 * * * *"
        assert again.validate() == []

    def test_json_is_accepted_too(self):
        wf = _sample()
        assert load_definition(wf.to_json()).id == wf.id

    def test_file_round_trip(self, tmp_path):
        wf = _sample()
        target = write_definition_file(wf, tmp_path / "rag.yaml")
        assert not list(tmp_path.glob(".*.tmp"))
        assert load_definition_file(target).id == wf.id

    @pytest.mark.parametrize(
        "text",
        ["- a\n- b\n", "just a string", "", "nodes: [{id: x, type: nonsense}]"],
    )
    def test_unusable_documents_raise_definition_error(self, text):
        with pytest.raises(DefinitionError):
            load_definition(text)

    def test_anchors_and_aliases_are_refused(self):
        bomb = "a: &a [1,2,3]\nb: *a\n"
        with pytest.raises(DefinitionError, match="anchors"):
            load_definition(bomb)

    def test_oversized_text_is_refused(self):
        with pytest.raises(DefinitionError, match="larger than"):
            load_definition("x: " + "a" * MAX_DEFINITION_BYTES)

    def test_python_object_tags_are_refused(self):
        with pytest.raises(DefinitionError):
            load_definition("id: !!python/object/apply:os.system ['echo x']")

    def test_bad_cron_is_a_definition_error(self):
        with pytest.raises(DefinitionError):
            load_definition("schedule: 'not a cron'\nnodes: []\nedges: []")

    def test_callables_in_config_cannot_be_exported(self):
        wf = (
            WorkflowBuilder("x")
            .start()
            .transform("t", transform=lambda v: v)
            .end()
            .build()
        )
        with pytest.raises(DefinitionError, match="cannot be exported"):
            dump_yaml(wf)


def _wf_with(node: WorkflowNode, *, extra_edges=()) -> WorkflowDefinition:
    wf = WorkflowDefinition(name="v")
    start = WorkflowNode(id="start", type=NodeType.START, label="s")
    end = WorkflowNode(id="end", type=NodeType.END, label="e")
    for n in (start, node, end):
        wf.add_node(n)
    wf.add_edge(WorkflowEdge(id="a", source_id="start", target_id=node.id))
    wf.add_edge(WorkflowEdge(id="b", source_id=node.id, target_id="end"))
    for edge in extra_edges:
        wf.add_edge(edge)
    return wf


def _n(node_type: NodeType, **config) -> WorkflowNode:
    return WorkflowNode(id="n", type=node_type, label="n", config=config)


class TestValidation:
    @pytest.mark.parametrize(
        ("node", "needle"),
        [
            (_n(NodeType.LLM), "LLM node n needs a config['prompt']"),
            (_n(NodeType.LLM, prompt="  "), "needs a config['prompt']"),
            (_n(NodeType.TEMPLATE), "needs a config['template']"),
            (_n(NodeType.HTTP), "needs a config['url']"),
            (
                _n(NodeType.HTTP, url="https://a.b/", method="TRACE"),
                "unsupported method",
            ),
            (_n(NodeType.ASSIGN), "assigns nothing"),
            (_n(NodeType.ASSIGN, assign={"input": 1}), "reserved name"),
            (_n(NodeType.ITERATION), "needs config['workflow']"),
            (_n(NodeType.CLASSIFIER, categories=["only-one"]), "at least two"),
            (_n(NodeType.CLASSIFIER, categories=["a", "a"]), "duplicate"),
        ],
    )
    def test_misconfigured_nodes_are_reported(self, node, needle):
        errors = _wf_with(node).validate()
        assert any(needle in e for e in errors), errors

    def test_valid_typed_nodes_pass(self):
        assert _wf_with(_n(NodeType.LLM, prompt="hi")).validate() == []
        assert _wf_with(_n(NodeType.HTTP, url="https://a.b/")).validate() == []

    def test_classifier_edges_need_labels_for_every_class(self):
        node = _n(NodeType.CLASSIFIER, categories=["a", "b"])
        errors = _wf_with(node).validate()
        assert any("every outgoing edge needs a label" in e for e in errors)

    def test_classifier_with_default_edge_covers_unlisted_classes(self):
        node = _n(NodeType.CLASSIFIER, categories=["a", "b"])
        wf = WorkflowDefinition(name="c")
        for n in (
            WorkflowNode(id="start", type=NodeType.START, label="s"),
            node,
            WorkflowNode(id="x", type=NodeType.END, label="x"),
        ):
            wf.add_node(n)
        wf.add_edge(WorkflowEdge(id="1", source_id="start", target_id="n"))
        wf.add_edge(
            WorkflowEdge(id="2", source_id="n", target_id="x", condition_label="a")
        )
        assert any("no edge for class 'b'" in e for e in wf.validate())
        wf.add_edge(
            WorkflowEdge(
                id="3", source_id="n", target_id="x", condition_label="default"
            )
        )
        assert wf.validate() == []

    def test_nested_iteration_workflow_is_validated(self):
        broken = {"id": "sub", "nodes": [], "edges": []}
        errors = _wf_with(_n(NodeType.ITERATION, workflow=broken)).validate()
        assert any("must have a START node" in e or "START node" in e for e in errors)

    def test_nesting_depth_is_bounded(self):
        inner = _sample().to_dict()
        for _ in range(8):
            wrapper = _wf_with(_n(NodeType.ITERATION, workflow=inner))
            inner = wrapper.to_dict()
        errors = WorkflowDefinition.from_dict(inner).validate()
        assert any("nesting deeper" in e for e in errors)

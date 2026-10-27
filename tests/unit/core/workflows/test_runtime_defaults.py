"""What the default catalog executor is given: plugin tools and credentials."""

import argparse
import json
from types import SimpleNamespace

import pytest

from baselith.core.config.workflows import WorkflowsConfig
from baselith.core.workflows.runtime import default_executor_factory


def _plugin(*tools, initialized=True, broken=False):
    def get_mcp_tools():
        if broken:
            raise RuntimeError("plugin exploded")
        return list(tools)

    return SimpleNamespace(
        is_initialized=lambda: initialized, get_mcp_tools=get_mcp_tools
    )


def _tool(name, category=None, handler=True):
    entry = {"name": name, "handler": (lambda **kw: name) if handler else None}
    if category is not None:
        entry["category"] = category
    return entry


@pytest.fixture
def registry(monkeypatch):
    holder = SimpleNamespace(plugins=[])

    class FakeServiceRegistry:
        @staticmethod
        def has(_cls):
            return True

        @staticmethod
        def get(_cls):
            return SimpleNamespace(get_all=lambda: holder.plugins)

    monkeypatch.setattr("baselith.core.di.ServiceRegistry", FakeServiceRegistry)
    return holder


def test_only_read_only_plugin_tools_reach_catalog_workflows(registry):
    registry.plugins = [
        _plugin(
            _tool("lookup", "read_only"),
            _tool("write_file", "mutating"),
            _tool("wipe", "destructive"),
            _tool("undeclared"),  # no category: gated as destructive everywhere
            _tool("nameless_handler", "read_only", handler=False),
        )
    ]
    executor = default_executor_factory(WorkflowsConfig())()
    assert set(executor._tools) == {"lookup"}
    assert executor._agents == {}


def test_uninitialized_and_broken_plugins_contribute_nothing(registry):
    registry.plugins = [
        _plugin(_tool("a", "read_only"), initialized=False),
        _plugin(_tool("b", "read_only"), broken=True),
        _plugin(_tool("c", "read_only")),
    ]
    assert set(default_executor_factory(WorkflowsConfig())()._tools) == {"c"}


def test_no_plugin_registry_means_no_tools(monkeypatch):
    class Absent:
        @staticmethod
        def has(_cls):
            return False

    monkeypatch.setattr("baselith.core.di.ServiceRegistry", Absent)
    assert default_executor_factory(WorkflowsConfig())()._tools == {}


class TestHttpCredentials:
    @staticmethod
    def _creds(value):
        config = WorkflowsConfig(WORKFLOWS_HTTP_CREDENTIALS=value)
        return default_executor_factory(config)()._services["http_credentials"]

    def test_valid_json_object(self):
        raw = json.dumps({"crm": {"Authorization": "Bearer x"}})
        assert self._creds(raw) == {"crm": {"Authorization": "Bearer x"}}

    @pytest.mark.parametrize(
        "raw", ["not json", "[1, 2]", '{"crm": "Bearer x"}', '{"crm": {"H": 1}}']
    )
    def test_malformed_credentials_yield_none_rather_than_a_guess(self, raw):
        assert self._creds(raw) == {}

    def test_unset_means_no_credentials(self):
        config = WorkflowsConfig()
        assert default_executor_factory(config)()._services["http_credentials"] == {}

    def test_secret_is_not_in_the_config_repr(self):
        config = WorkflowsConfig(
            WORKFLOWS_HTTP_CREDENTIALS='{"crm": {"Authorization": "Bearer SECRETVALUE"}}',
            WORKFLOWS_TRIGGER_SECRET="MASTERVALUE",
        )
        assert "SECRETVALUE" not in repr(config) and "MASTERVALUE" not in repr(config)


async def test_default_retriever_embeds_off_loop_and_shapes_hits(monkeypatch):
    from baselith.core.workflows import nodes_ai

    seen = {}

    class Embedder:
        pass

    async def encode(embedder, texts):
        seen["texts"] = texts
        return [[0.25, 0.5]]

    class Store:
        async def search(self, **kwargs):
            seen["search"] = kwargs
            doc = SimpleNamespace(id="d1", content="alpha", metadata={"k": "v"})
            return [SimpleNamespace(document=doc, score=0.7)]

    monkeypatch.setattr("baselith.core.nlp.models.get_embedder", Embedder)
    monkeypatch.setattr("baselith.core.nlp.roles.aencode_query", encode)
    monkeypatch.setattr(
        "baselith.core.services.vectorstore.service.get_vectorstore_service", Store
    )

    hits = await nodes_ai._default_retriever(
        "what is alpha", top_k=3, collection="docs"
    )

    assert hits == [
        {"id": "d1", "content": "alpha", "score": 0.7, "metadata": {"k": "v"}}
    ]
    assert seen["texts"] == ["what is alpha"]
    assert seen["search"]["k"] == 3 and seen["search"]["collection_name"] == "docs"
    assert seen["search"]["query_vector"] == [0.25, 0.5]


def test_cli_parser_exposes_every_subcommand():
    from baselith.core.cli.commands.workflow import register_parser

    root = argparse.ArgumentParser()
    register_parser(root.add_subparsers(dest="command"), argparse.HelpFormatter)

    parsed = root.parse_args(["workflow", "validate", "a.yaml", "b.yaml"])
    assert parsed.workflow_command == "validate" and parsed.files == [
        "a.yaml",
        "b.yaml",
    ]
    parsed = root.parse_args(["workflow", "run", "w.yaml", "--input", "{}"])
    assert parsed.file == "w.yaml" and parsed.input == "{}"
    parsed = root.parse_args(
        ["workflow", "export", "w.yaml", "--to", "json", "-o", "o"]
    )
    assert (parsed.to, parsed.output) == ("json", "o")
    assert root.parse_args(["workflow", "list", "--dir", "d"]).dir == "d"
    assert root.parse_args(["workflow", "trigger-secret", "x"]).workflow_id == "x"
    assert (
        root.parse_args(["workflow", "trigger-sign", "x", "--body-file", "b"]).body_file
        == "b"
    )

"""Template scope and rendering for workflow nodes."""

import pytest

from baselith.core.workflows._scope import (
    MAX_RENDERED_CHARS,
    TemplateError,
    build_scope,
    render_template,
    render_value,
    resolve_path,
)
from baselith.core.workflows.executor import (
    ExecutionContext,
    ExecutionStatus,
    NodeResult,
)

pytestmark = [pytest.mark.unit]

SCOPE = {
    "input": "hello",
    "output": {"score": 0.5},
    "nodes": {
        "search": [{"content": "alpha", "n": 1}, {"content": "beta", "n": 2}],
        "fetch-user": {"name": "Ada", "tags": ["x", "y"]},
    },
    "count": 3,
    "flag": True,
    "nothing": None,
}


class TestResolvePath:
    def test_walks_keys_and_indexes(self):
        assert resolve_path(SCOPE, "nodes.search[1].content") == "beta"

    def test_hyphenated_node_ids_and_quoted_keys(self):
        assert resolve_path(SCOPE, "nodes.fetch-user.name") == "Ada"
        assert resolve_path(SCOPE, 'nodes["fetch-user"].tags[-1]') == "y"

    def test_none_is_a_real_value(self):
        assert resolve_path(SCOPE, "nothing") is None

    @pytest.mark.parametrize(
        "path",
        [
            "missing",
            "nodes.search[9]",
            "nodes.search.content",
            "input.x",
            "",
            "a..b",
            "a.",
            "[0]x",
        ],
    )
    def test_unresolved_or_malformed_raises(self, path):
        with pytest.raises(TemplateError):
            resolve_path(SCOPE, path)

    def test_never_reads_attributes_or_calls(self):
        with pytest.raises(TemplateError):
            resolve_path({"s": "text"}, "s.upper")
        with pytest.raises(TemplateError):
            resolve_path({"s": "text"}, "s.__class__")

    def test_string_is_not_indexable(self):
        with pytest.raises(TemplateError):
            resolve_path(SCOPE, "input[0]")


class TestRenderTemplate:
    def test_scalars_and_structures(self):
        out = render_template("{{ input }} {{ count }} {{ flag }} {{ output }}", SCOPE)
        assert out == 'hello 3 true {"score": 0.5}'

    def test_none_renders_empty(self):
        assert render_template("[{{ nothing }}]", SCOPE) == "[]"

    def test_filters_chain(self):
        tpl = "{{ nodes.search | pluck(content) | join(', ') | upper }}"
        assert render_template(tpl, SCOPE) == "ALPHA, BETA"
        assert render_template("{{ nodes.search | length }}", SCOPE) == "2"
        assert render_template("{{ nodes.fetch-user.tags | first }}", SCOPE) == "x"
        assert render_template("{{ input | truncate(2) }}", SCOPE) == "he"
        assert render_template("{{ nodes.fetch-user | json }}", SCOPE).startswith(
            '{"name"'
        )

    def test_pipe_inside_quotes_is_not_a_filter(self):
        assert render_template("{{ missing | default('a|b') }}", SCOPE) == "a|b"

    def test_default_covers_missing_none_and_empty(self):
        assert render_template("{{ nope | default('x') }}", SCOPE) == "x"
        assert render_template("{{ nothing | default('x') }}", SCOPE) == "x"
        assert render_template("{{ input | default('x') }}", SCOPE) == "hello"

    def test_unresolved_reference_raises_not_blank(self):
        with pytest.raises(TemplateError, match="unresolved"):
            render_template("prompt {{ nodes.gone }}", SCOPE)

    def test_filter_on_missing_raises(self):
        with pytest.raises(TemplateError):
            render_template("{{ nope | upper }}", SCOPE)

    def test_unknown_filter_raises(self):
        with pytest.raises(TemplateError, match="unknown filter"):
            render_template("{{ input | system }}", SCOPE)

    def test_pluck_requires_key_in_every_item(self):
        with pytest.raises(TemplateError):
            render_template("{{ nodes.search | pluck(zzz) }}", SCOPE)

    def test_size_cap(self):
        big = {"x": "a" * MAX_RENDERED_CHARS}
        with pytest.raises(TemplateError, match="exceeds"):
            render_template("{{ x }}{{ x }}", big)


class TestRenderValue:
    def test_single_placeholder_keeps_type(self):
        assert render_value("{{ nodes.search }}", SCOPE) == SCOPE["nodes"]["search"]
        assert render_value("{{ count }}", SCOPE) == 3
        assert render_value("  {{ flag }}  ", SCOPE) is True

    def test_mixed_text_is_a_string(self):
        assert render_value("n={{ count }}", SCOPE) == "n=3"

    def test_two_adjacent_placeholders_are_not_one(self):
        assert render_value("{{ input }}-{{ count }}", SCOPE) == "hello-3"

    def test_recurses_into_lists_and_dict_values_only(self):
        value = {"{{ keep }}": ["{{ count }}", {"a": "{{ input }}"}, 7]}
        assert render_value(value, SCOPE) == {"{{ keep }}": [3, {"a": "hello"}, 7]}


def test_build_scope_reflects_context():
    ctx = ExecutionContext(workflow_id="w")
    ctx.set_variable("input", "q")
    ctx.set_variable("mine", 1)
    ctx.node_results["a"] = NodeResult("a", ExecutionStatus.COMPLETED, output="out-a")
    scope = build_scope(ctx)
    assert scope["input"] == "q" and scope["mine"] == 1
    assert scope["output"] == "out-a"
    assert scope["nodes"] == {"a": "out-a"}
    scope["nodes"]["a"] = "mutated"
    assert ctx.node_results["a"].output == "out-a"

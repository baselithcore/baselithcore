"""Unit tests for ``core.a2a.a2ui``."""

from __future__ import annotations

import pytest

from core.a2a.a2ui import (
    A2UI_SCHEMA_VERSION,
    MAX_TREE_DEPTH,
    MAX_TREE_NODES,
    A2UIBlueprint,
    A2UIValidationError,
    Badge,
    Container,
    validate_blueprint,
)


def _container_payload(children: list[dict]) -> dict:
    return {
        "schema_version": "a2ui/v1",
        "root": {"type": "container", "children": children},
    }


class TestSchemaValidation:
    def test_valid_minimal_blueprint(self) -> None:
        payload = _container_payload([{"type": "text", "content": "hello"}])
        bp = validate_blueprint(payload)
        assert isinstance(bp, A2UIBlueprint)
        assert bp.schema_version == "a2ui/v1"

    def test_unknown_component_type_rejected(self) -> None:
        payload = _container_payload([{"type": "iframe", "src": "evil"}])
        with pytest.raises(Exception):
            validate_blueprint(payload)

    def test_extra_field_rejected(self) -> None:
        payload = {
            "schema_version": "a2ui/v1",
            "root": {
                "type": "text",
                "content": "x",
                "onclick": "alert(1)",
            },
        }
        with pytest.raises(Exception):
            validate_blueprint(payload)

    def test_wrong_schema_version_rejected(self) -> None:
        payload = {
            "schema_version": "a2ui/v2",
            "root": {"type": "text", "content": "x"},
        }
        with pytest.raises(Exception):
            validate_blueprint(payload)

    def test_form_with_inputs_and_button(self) -> None:
        payload = {
            "schema_version": "a2ui/v1",
            "root": {
                "type": "form",
                "action": "submit_feedback",
                "children": [
                    {
                        "type": "input",
                        "name": "email",
                        "input_type": "email",
                        "required": True,
                    },
                    {
                        "type": "button",
                        "label": "Send",
                        "action": "submit",
                    },
                ],
            },
        }
        bp = validate_blueprint(payload)
        assert bp.root.type == "form"

    def test_list_with_items(self) -> None:
        payload = {
            "schema_version": "a2ui/v1",
            "root": {
                "type": "list",
                "items": [
                    {
                        "type": "list_item",
                        "children": [{"type": "text", "content": "a"}],
                    },
                    {
                        "type": "list_item",
                        "children": [{"type": "text", "content": "b"}],
                    },
                ],
            },
        }
        bp = validate_blueprint(payload)
        assert bp.root.type == "list"

    def test_badge_default_tone(self) -> None:
        bp = validate_blueprint(_container_payload([{"type": "badge", "label": "new"}]))
        assert isinstance(bp.root, Container)
        first = bp.root.children[0]
        assert isinstance(first, Badge)
        assert first.tone == "neutral"


class TestBoundsEnforcement:
    def test_max_depth_enforced(self) -> None:
        innermost = {"type": "text", "content": "x"}
        node: dict = innermost
        for _ in range(MAX_TREE_DEPTH + 2):
            node = {"type": "container", "children": [node]}
        payload = {"schema_version": "a2ui/v1", "root": node}
        with pytest.raises(A2UIValidationError):
            validate_blueprint(payload)

    def test_max_nodes_enforced(self) -> None:
        children = [{"type": "text", "content": "x"} for _ in range(MAX_TREE_NODES + 5)]
        with pytest.raises(A2UIValidationError):
            validate_blueprint(_container_payload(children))

    def test_within_bounds_allowed(self) -> None:
        children = [
            {"type": "text", "content": "x"} for _ in range(MAX_TREE_NODES // 2)
        ]
        bp = validate_blueprint(_container_payload(children))
        assert isinstance(bp.root, Container)
        assert len(bp.root.children) == MAX_TREE_NODES // 2


class TestUrlSchemeAllowList:
    @pytest.mark.parametrize(
        "href",
        [
            "javascript:alert(document.cookie)",
            "JAVASCRIPT:alert(1)",
            "java\tscript:alert(1)",
            "  javascript:alert(1)",
            "data:text/html,<script>alert(1)</script>",
            "vbscript:msgbox(1)",
        ],
    )
    def test_link_rejects_dangerous_schemes(self, href: str) -> None:
        payload = _container_payload([{"type": "link", "label": "x", "href": href}])
        with pytest.raises(Exception):
            validate_blueprint(payload)

    @pytest.mark.parametrize(
        "href",
        [
            "https://example.com/ok",
            "http://example.com",
            "mailto:a@example.com",
            "/relative/path",
            "#anchor",
        ],
    )
    def test_link_allows_safe_urls(self, href: str) -> None:
        payload = _container_payload([{"type": "link", "label": "x", "href": href}])
        validate_blueprint(payload)

    def test_image_rejects_dangerous_scheme(self) -> None:
        payload = _container_payload(
            [{"type": "image", "src": "data:text/html,<script>alert(1)</script>"}]
        )
        with pytest.raises(Exception):
            validate_blueprint(payload)

    def test_image_allows_https(self) -> None:
        payload = _container_payload(
            [{"type": "image", "src": "https://cdn.example.com/a.png"}]
        )
        validate_blueprint(payload)


class TestModuleConstants:
    def test_schema_version_constant(self) -> None:
        assert A2UI_SCHEMA_VERSION == "a2ui/v1"

    def test_node_cap_sane(self) -> None:
        assert MAX_TREE_NODES >= 64

    def test_depth_cap_sane(self) -> None:
        assert MAX_TREE_DEPTH >= 8

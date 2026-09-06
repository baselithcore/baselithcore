"""Declared plugin permissions: parsing, matching, and the enforcement mode.

A plugin runs in-process with the host's full authority. The manifest proves
*which code* is running (``integrity_sha256`` + the Ed25519 signature); these
permissions declare *what that code may do*.

The rollout is deliberately staged, so an existing deployment keeps working:
declare → observe → enforce. ``off``/``warn`` change nothing at runtime;
``enforce`` is opt-in through ``BASELITH_PLUGIN_PERMISSIONS``, the same shape
as ``BASELITH_REQUIRE_SIGNED_PLUGINS``.
"""

from __future__ import annotations

import pytest

from core.plugins.permissions import (
    PermissionMode,
    PluginPermissions,
    parse_permissions,
    resolve_permission_mode,
)


class TestParsing:
    def test_a_manifest_without_a_block_is_undeclared(self) -> None:
        permissions = parse_permissions(None)

        assert permissions.declared is False
        assert permissions.network_egress == ()

    def test_an_empty_block_is_declared_and_denies_everything(self) -> None:
        """`permissions: {}` is a statement, not an omission."""
        permissions = parse_permissions({})

        assert permissions.declared is True
        assert permissions.allows_host("api.example.com") is False

    def test_every_field_is_parsed(self) -> None:
        permissions = parse_permissions(
            {
                "network": {"egress": ["api.github.com", "*.openai.com"]},
                "tools": ["search_knowledge_base"],
                "secrets": ["GITHUB_TOKEN"],
                "filesystem": ["./data/plugins/demo"],
            }
        )

        assert permissions.network_egress == ("api.github.com", "*.openai.com")
        assert permissions.tools == ("search_knowledge_base",)
        assert permissions.secrets == ("GITHUB_TOKEN",)
        assert permissions.filesystem == ("./data/plugins/demo",)

    @pytest.mark.parametrize(
        "raw", [{"network": "nope"}, {"tools": 5}, {"secrets": {"a": 1}}, "garbage", 7]
    )
    def test_malformed_input_never_raises(self, raw: object) -> None:
        """A bad manifest must not break plugin loading; it just grants nothing."""
        permissions = parse_permissions(raw)

        assert permissions.allows_host("api.example.com") is False

    def test_entries_are_normalised(self) -> None:
        permissions = parse_permissions(
            {"network": {"egress": ["  API.GitHub.com  ", "", None, 5]}}
        )

        assert permissions.network_egress == ("api.github.com",)


class TestHostMatching:
    @pytest.mark.parametrize(
        ("pattern", "host", "allowed"),
        [
            ("api.github.com", "api.github.com", True),
            ("api.github.com", "API.GITHUB.COM", True),
            ("api.github.com", "evil.com", False),
            ("*.openai.com", "api.openai.com", True),
            ("*.openai.com", "a.b.openai.com", True),
            ("*.openai.com", "openai.com", False),
            ("*.openai.com", "notopenai.com", False),
            # The bypass a naive suffix check would allow.
            ("*.openai.com", "api.openai.com.evil.net", False),
            ("*", "anything.example", True),
        ],
    )
    def test_patterns(self, pattern: str, host: str, allowed: bool) -> None:
        permissions = parse_permissions({"network": {"egress": [pattern]}})

        assert permissions.allows_host(host) is allowed

    def test_an_undeclared_plugin_allows_nothing(self) -> None:
        assert parse_permissions(None).allows_host("api.github.com") is False


class TestToolAndSecretMatching:
    def test_exact_tool_match(self) -> None:
        permissions = parse_permissions({"tools": ["scrape_url"]})

        assert permissions.allows_tool("scrape_url") is True
        assert permissions.allows_tool("execute_code") is False

    def test_tool_wildcard(self) -> None:
        assert parse_permissions({"tools": ["*"]}).allows_tool("anything") is True

    def test_secret_match_is_case_sensitive_like_the_environment(self) -> None:
        permissions = parse_permissions({"secrets": ["GITHUB_TOKEN"]})

        assert permissions.allows_secret("GITHUB_TOKEN") is True
        assert permissions.allows_secret("github_token") is False


class TestMode:
    @pytest.mark.parametrize(
        ("value", "expected"),
        [
            (None, PermissionMode.WARN),
            ("", PermissionMode.WARN),
            ("warn", PermissionMode.WARN),
            ("off", PermissionMode.OFF),
            ("enforce", PermissionMode.ENFORCE),
            ("ENFORCE", PermissionMode.ENFORCE),
            ("true", PermissionMode.ENFORCE),
            ("nonsense", PermissionMode.WARN),
        ],
    )
    def test_resolution(self, value: str | None, expected: PermissionMode) -> None:
        assert resolve_permission_mode(value) is expected

    def test_warn_is_the_default_so_nothing_breaks_on_upgrade(self) -> None:
        """The whole point of the staged rollout."""
        assert resolve_permission_mode(None) is PermissionMode.WARN
        assert PermissionMode.WARN.enforces is False
        assert PermissionMode.ENFORCE.enforces is True
        assert PermissionMode.OFF.enforces is False


class TestEgressDecision:
    def test_off_allows_an_undeclared_plugin(self) -> None:
        permissions = parse_permissions(None)

        assert permissions.egress_denied("evil.example", PermissionMode.OFF) is False

    def test_warn_allows_but_reports(self) -> None:
        permissions = parse_permissions({"network": {"egress": ["api.github.com"]}})

        assert permissions.egress_denied("evil.example", PermissionMode.WARN) is False

    def test_enforce_denies_an_undeclared_host(self) -> None:
        permissions = parse_permissions({"network": {"egress": ["api.github.com"]}})

        assert permissions.egress_denied("evil.example", PermissionMode.ENFORCE) is True
        assert (
            permissions.egress_denied("api.github.com", PermissionMode.ENFORCE) is False
        )

    def test_enforce_leaves_a_plugin_that_declared_nothing_alone(self) -> None:
        """Undeclared means "not migrated yet", not "denied everything".

        Turning the flag on must not brick every plugin written before the
        block existed; it gates the ones that opted in.
        """
        permissions = parse_permissions(None)

        assert (
            permissions.egress_denied("evil.example", PermissionMode.ENFORCE) is False
        )


class TestSummary:
    def test_summary_is_marketplace_friendly(self) -> None:
        permissions = parse_permissions(
            {"network": {"egress": ["api.github.com"]}, "secrets": ["GITHUB_TOKEN"]}
        )

        summary = permissions.summary()

        assert summary["declared"] is True
        assert summary["network_egress"] == ["api.github.com"]
        assert summary["secrets"] == ["GITHUB_TOKEN"]
        assert summary["tools"] == []

    def test_an_undeclared_plugin_says_so(self) -> None:
        assert parse_permissions(None).summary()["declared"] is False


def test_permissions_are_immutable() -> None:
    """A loaded plugin must not be able to widen its own grant at runtime."""
    permissions = PluginPermissions(declared=True, network_egress=("a.example",))

    with pytest.raises(Exception):
        permissions.network_egress = ("b.example",)  # type: ignore[misc]

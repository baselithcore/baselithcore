"""Tool and secret access held to a plugin's declared permissions.

The staged rollout is what these tests are really about. Every guard has to
answer four questions in the same order — is a plugin bound, what mode, did it
declare anything, does the declaration cover this — and getting the third one
wrong is how "enforce" starts refusing plugins that were promised they would be
left alone.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest

from core.context import reset_plugin_context, set_plugin_context
from core.plugins.access import (
    SecretNotPermittedError,
    ToolNotPermittedError,
    check_tool_permitted,
    install_secret_guard,
    uninstall_secret_guard,
)
from core.plugins.permission_runtime import set_permissions_resolver
from core.plugins.permissions import parse_permissions
from core.security.secrets import get_secret


@pytest.fixture(autouse=True)
def _clean_guards(monkeypatch) -> Iterator[None]:
    monkeypatch.delenv("BASELITH_PLUGIN_PERMISSIONS", raising=False)
    uninstall_secret_guard()
    set_permissions_resolver(None)
    yield
    uninstall_secret_guard()
    set_permissions_resolver(None)


def _declare(**blocks) -> None:
    """Register one plugin, ``acme``, with the given manifest block."""
    permissions = parse_permissions(blocks) if blocks else None
    set_permissions_resolver(lambda name: permissions if name == "acme" else None)


class _AsPlugin:
    """Bind the plugin context for the duration of a block."""

    def __init__(self, name: str = "acme") -> None:
        self._name = name

    def __enter__(self):
        self._token = set_plugin_context(self._name)
        return self

    def __exit__(self, *exc):
        reset_plugin_context(self._token)
        return False


class TestToolAccess:
    def test_core_traffic_is_never_gated(self, monkeypatch):
        """No plugin bound: this is the framework's own call."""
        monkeypatch.setenv("BASELITH_PLUGIN_PERMISSIONS", "enforce")
        _declare(tools=["allowed"])
        check_tool_permitted("anything")

    def test_a_declared_tool_is_allowed(self, monkeypatch):
        monkeypatch.setenv("BASELITH_PLUGIN_PERMISSIONS", "enforce")
        _declare(tools=["search"])
        with _AsPlugin():
            check_tool_permitted("search")

    def test_an_undeclared_tool_is_refused_under_enforce(self, monkeypatch):
        monkeypatch.setenv("BASELITH_PLUGIN_PERMISSIONS", "enforce")
        _declare(tools=["search"])
        with _AsPlugin(), pytest.raises(ToolNotPermittedError) as excinfo:
            check_tool_permitted("charge_card")
        assert excinfo.value.tool == "charge_card"
        assert "permissions.tools" in str(excinfo.value)

    def test_warn_mode_lets_it_through(self, monkeypatch):
        monkeypatch.setenv("BASELITH_PLUGIN_PERMISSIONS", "warn")
        _declare(tools=["search"])
        with _AsPlugin():
            check_tool_permitted("charge_card")

    def test_warn_is_the_default(self):
        _declare(tools=["search"])
        with _AsPlugin():
            check_tool_permitted("charge_card")

    def test_off_mode_never_consults_the_declaration(self, monkeypatch):
        monkeypatch.setenv("BASELITH_PLUGIN_PERMISSIONS", "off")
        _declare(tools=["search"])
        with _AsPlugin():
            check_tool_permitted("charge_card")

    def test_a_plugin_that_declared_nothing_is_untouched(self, monkeypatch):
        """Undeclared means "not migrated yet", never "denied everything"."""
        monkeypatch.setenv("BASELITH_PLUGIN_PERMISSIONS", "enforce")
        set_permissions_resolver(lambda name: None)
        with _AsPlugin():
            check_tool_permitted("charge_card")

    def test_an_explicit_empty_list_denies(self, monkeypatch):
        """`tools: []` is a statement; absence of the block is not."""
        monkeypatch.setenv("BASELITH_PLUGIN_PERMISSIONS", "enforce")
        _declare(tools=[])
        with _AsPlugin(), pytest.raises(ToolNotPermittedError):
            check_tool_permitted("search")

    def test_a_wildcard_covers_everything(self, monkeypatch):
        monkeypatch.setenv("BASELITH_PLUGIN_PERMISSIONS", "enforce")
        _declare(tools=["*"])
        with _AsPlugin():
            check_tool_permitted("anything_at_all")

    def test_another_plugins_declaration_does_not_apply(self, monkeypatch):
        monkeypatch.setenv("BASELITH_PLUGIN_PERMISSIONS", "enforce")
        _declare(tools=[])
        with _AsPlugin("other"):
            check_tool_permitted("search")  # unknown plugin: not migrated

    def test_a_broken_resolver_does_not_break_the_tool_path(self, monkeypatch):
        monkeypatch.setenv("BASELITH_PLUGIN_PERMISSIONS", "enforce")

        def boom(name):
            raise RuntimeError("registry is down")

        set_permissions_resolver(boom)
        with _AsPlugin():
            check_tool_permitted("search")


class TestSecretAccess:
    def test_a_declared_secret_is_readable(self, monkeypatch):
        monkeypatch.setenv("BASELITH_PLUGIN_PERMISSIONS", "enforce")
        monkeypatch.setenv("ACME_TOKEN", "s3cret")
        _declare(secrets=["ACME_TOKEN"])
        install_secret_guard()
        with _AsPlugin():
            value = get_secret("ACME_TOKEN")
        assert value is not None
        assert value.get_secret_value() == "s3cret"

    def test_an_undeclared_secret_is_refused(self, monkeypatch):
        monkeypatch.setenv("BASELITH_PLUGIN_PERMISSIONS", "enforce")
        monkeypatch.setenv("OTHER_TOKEN", "s3cret")
        _declare(secrets=["ACME_TOKEN"])
        install_secret_guard()
        with _AsPlugin(), pytest.raises(SecretNotPermittedError) as excinfo:
            get_secret("OTHER_TOKEN")
        assert excinfo.value.secret == "OTHER_TOKEN"

    def test_a_refused_read_never_resolves_the_value(self, monkeypatch):
        """The guard runs before the provider, so the credential is not read."""
        monkeypatch.setenv("BASELITH_PLUGIN_PERMISSIONS", "enforce")
        _declare(secrets=[])
        install_secret_guard()

        resolved: list[str] = []

        class _Spy:
            def get_secret(self, name):
                resolved.append(name)
                return None

        monkeypatch.setattr(
            "core.security.secrets.get_secrets_provider", lambda: _Spy()
        )
        with _AsPlugin(), pytest.raises(SecretNotPermittedError):
            get_secret("ACME_TOKEN")
        assert resolved == []

    def test_core_reads_are_never_gated(self, monkeypatch):
        monkeypatch.setenv("BASELITH_PLUGIN_PERMISSIONS", "enforce")
        monkeypatch.setenv("DB_PASSWORD", "s3cret")
        _declare(secrets=[])
        install_secret_guard()
        assert get_secret("DB_PASSWORD") is not None

    def test_secret_names_are_case_sensitive(self, monkeypatch):
        monkeypatch.setenv("BASELITH_PLUGIN_PERMISSIONS", "enforce")
        _declare(secrets=["ACME_TOKEN"])
        install_secret_guard()
        with _AsPlugin(), pytest.raises(SecretNotPermittedError):
            get_secret("acme_token")

    def test_uninstalling_restores_the_plain_accessor(self, monkeypatch):
        monkeypatch.setenv("BASELITH_PLUGIN_PERMISSIONS", "enforce")
        monkeypatch.setenv("OTHER_TOKEN", "s3cret")
        _declare(secrets=[])
        install_secret_guard()
        uninstall_secret_guard()
        with _AsPlugin():
            assert get_secret("OTHER_TOKEN") is not None


class TestEnforcementChokepoint:
    async def test_the_orchestration_gate_consults_the_declaration(self, monkeypatch):
        """The tool guard has to run where tools are actually invoked."""
        from core.orchestration.enforcement import enforce_tool_invocation

        monkeypatch.setenv("BASELITH_PLUGIN_PERMISSIONS", "enforce")
        _declare(tools=["search"])
        with _AsPlugin(), pytest.raises(ToolNotPermittedError):
            await enforce_tool_invocation({"tenant_id": "t"}, "charge_card")

    async def test_a_declared_tool_passes_the_gate(self, monkeypatch):
        from core.orchestration.enforcement import enforce_tool_invocation

        monkeypatch.setenv("BASELITH_PLUGIN_PERMISSIONS", "enforce")
        _declare(tools=["search"])
        with _AsPlugin():
            await enforce_tool_invocation({"tenant_id": "t"}, "search")


class TestGuardInstallation:
    def test_install_wires_the_registry(self, monkeypatch):
        from core.plugins.guards import install_plugin_guards, uninstall_plugin_guards

        monkeypatch.setenv("BASELITH_PLUGIN_PERMISSIONS", "enforce")

        class _Plugin:
            metadata = type(
                "_M", (), {"permissions": parse_permissions({"tools": ["search"]})}
            )()

        class _Registry:
            def get(self, name):
                return _Plugin() if name == "acme" else None

        install_plugin_guards(_Registry())
        try:
            with _AsPlugin(), pytest.raises(ToolNotPermittedError):
                check_tool_permitted("charge_card")
        finally:
            uninstall_plugin_guards()

    def test_uninstall_clears_everything(self, monkeypatch):
        from core.plugins.guards import install_plugin_guards, uninstall_plugin_guards

        monkeypatch.setenv("BASELITH_PLUGIN_PERMISSIONS", "enforce")

        class _Registry:
            def get(self, name):
                return None

        install_plugin_guards(_Registry())
        uninstall_plugin_guards()
        with _AsPlugin():
            check_tool_permitted("anything")

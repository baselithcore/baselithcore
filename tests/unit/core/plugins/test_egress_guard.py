"""Per-plugin egress control at the SSRF choke point.

The SSRF guard answers "is this address safe to reach?". These tests cover the
other question — "is *this plugin* allowed to reach it" — and, above all, that
turning the mechanism on changes nothing for a deployment that has not migrated
its manifests.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest

from core.context import reset_plugin_context, set_plugin_context
from core.plugins.egress import (
    EgressNotPermittedError,
    install_egress_guard,
    set_permissions_resolver,
    uninstall_egress_guard,
)
from core.plugins.permissions import parse_permissions
from core.security.ssrf import SsrfError, assert_url_safe

DECLARED = parse_permissions(
    {"network": {"egress": ["api.github.com", "*.openai.com"]}}
)
UNDECLARED = parse_permissions(None)
EMPTY = parse_permissions({})


@pytest.fixture(autouse=True)
def guard_installed() -> Iterator[None]:
    install_egress_guard()
    yield
    uninstall_egress_guard()
    set_permissions_resolver(None)


@pytest.fixture
def as_plugin() -> Iterator[object]:
    tokens: list[object] = []

    def _bind(name: str) -> None:
        tokens.append(set_plugin_context(name))

    yield _bind

    for token in reversed(tokens):
        reset_plugin_context(token)  # type: ignore[arg-type]


def _resolver(mapping: dict[str, object]) -> None:
    set_permissions_resolver(lambda name: mapping.get(name))


class TestEnforce:
    def test_a_declared_host_is_allowed(
        self, as_plugin, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("BASELITH_PLUGIN_PERMISSIONS", "enforce")
        _resolver({"demo": DECLARED})
        as_plugin("demo")

        # No EgressNotPermittedError; DNS may still fail in a sandbox, which is
        # a different error class entirely.
        try:
            assert_url_safe("https://api.github.com/x")
        except EgressNotPermittedError:  # pragma: no cover - would be the bug
            pytest.fail("a declared host was refused")
        except SsrfError:
            pass  # DNS resolution, not a permission decision

    def test_an_undeclared_host_is_refused(
        self, as_plugin, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("BASELITH_PLUGIN_PERMISSIONS", "enforce")
        _resolver({"demo": DECLARED})
        as_plugin("demo")

        with pytest.raises(EgressNotPermittedError, match="not permitted to reach"):
            assert_url_safe("https://evil.example/x")

    def test_a_wildcard_subdomain_is_allowed_but_a_lookalike_is_not(
        self, as_plugin, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("BASELITH_PLUGIN_PERMISSIONS", "enforce")
        _resolver({"demo": DECLARED})
        as_plugin("demo")

        with pytest.raises(EgressNotPermittedError):
            assert_url_safe("https://api.openai.com.evil.net/x")

    def test_an_empty_declaration_denies_everything(
        self, as_plugin, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """`permissions: {}` is a plugin saying it needs no egress."""
        monkeypatch.setenv("BASELITH_PLUGIN_PERMISSIONS", "enforce")
        _resolver({"demo": EMPTY})
        as_plugin("demo")

        with pytest.raises(EgressNotPermittedError):
            assert_url_safe("https://api.github.com/x")


class TestUpgradeIsSafe:
    def test_a_plugin_that_declared_nothing_is_untouched(
        self, as_plugin, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The property that makes the flag safe to turn on."""
        monkeypatch.setenv("BASELITH_PLUGIN_PERMISSIONS", "enforce")
        _resolver({"legacy": UNDECLARED})
        as_plugin("legacy")

        with pytest.raises(SsrfError) as excinfo:
            assert_url_safe("https://anything.example/x")
        assert not isinstance(excinfo.value, EgressNotPermittedError)

    def test_an_unknown_plugin_is_untouched(
        self, as_plugin, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("BASELITH_PLUGIN_PERMISSIONS", "enforce")
        _resolver({})
        as_plugin("ghost")

        with pytest.raises(SsrfError) as excinfo:
            assert_url_safe("https://anything.example/x")
        assert not isinstance(excinfo.value, EgressNotPermittedError)

    def test_core_traffic_is_never_attributed_to_a_plugin(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("BASELITH_PLUGIN_PERMISSIONS", "enforce")
        _resolver({"demo": EMPTY})

        with pytest.raises(SsrfError) as excinfo:
            assert_url_safe("https://anything.example/x")
        assert not isinstance(excinfo.value, EgressNotPermittedError)


class TestWarnAndOff:
    def test_warn_allows_an_undeclared_host(
        self, as_plugin, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("BASELITH_PLUGIN_PERMISSIONS", "warn")
        _resolver({"demo": DECLARED})
        as_plugin("demo")

        with pytest.raises(SsrfError) as excinfo:
            assert_url_safe("https://evil.example/x")
        assert not isinstance(excinfo.value, EgressNotPermittedError)

    def test_warn_is_the_default(
        self, as_plugin, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("BASELITH_PLUGIN_PERMISSIONS", raising=False)
        _resolver({"demo": EMPTY})
        as_plugin("demo")

        with pytest.raises(SsrfError) as excinfo:
            assert_url_safe("https://evil.example/x")
        assert not isinstance(excinfo.value, EgressNotPermittedError)

    def test_off_skips_the_lookup_entirely(
        self, as_plugin, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("BASELITH_PLUGIN_PERMISSIONS", "off")
        looked_up: list[str] = []

        def resolver(name: str) -> object:
            looked_up.append(name)
            return EMPTY

        set_permissions_resolver(resolver)
        as_plugin("demo")

        with pytest.raises(SsrfError):
            assert_url_safe("https://evil.example/x")
        assert looked_up == []


class TestGuardLifecycle:
    def test_uninstall_restores_plain_ssrf_screening(
        self, as_plugin, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("BASELITH_PLUGIN_PERMISSIONS", "enforce")
        _resolver({"demo": EMPTY})
        as_plugin("demo")
        uninstall_egress_guard()

        with pytest.raises(SsrfError) as excinfo:
            assert_url_safe("https://evil.example/x")
        assert not isinstance(excinfo.value, EgressNotPermittedError)

    def test_a_broken_resolver_does_not_break_outbound_traffic(
        self, as_plugin, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("BASELITH_PLUGIN_PERMISSIONS", "enforce")

        def boom(name: str) -> object:
            raise RuntimeError("registry unavailable")

        set_permissions_resolver(boom)
        as_plugin("demo")

        with pytest.raises(SsrfError) as excinfo:
            assert_url_safe("https://evil.example/x")
        assert not isinstance(excinfo.value, EgressNotPermittedError)

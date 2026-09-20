"""API stability tiers: resolution, marking and the deprecation decorator."""

from __future__ import annotations

import warnings

import pytest

from core.stability import (
    DEFAULT_STABILITY,
    PACKAGE_STABILITY,
    STABILITY_ATTRIBUTE,
    Stability,
    beta,
    deprecated,
    experimental,
    stability_of,
    stable,
    symbol_stability,
)


class TestStabilityResolution:
    """Which promise governs a given dotted name."""

    def test_a_package_resolves_to_its_declared_tier(self) -> None:
        assert stability_of("core.agent") is Stability.STABLE
        assert stability_of("core.swarm") is Stability.EXPERIMENTAL
        assert stability_of("core.agents") is Stability.DEPRECATED

    def test_a_submodule_inherits_its_package(self) -> None:
        assert stability_of("core.memory.hybrid_search") is Stability.BETA

    def test_the_most_specific_prefix_wins(self) -> None:
        """A subpackage can be classified apart from its parent."""
        table = dict(PACKAGE_STABILITY) | {"core.swarm.auction": Stability.BETA}
        original = stability_of.__globals__["PACKAGE_STABILITY"]
        stability_of.__globals__["PACKAGE_STABILITY"] = table
        try:
            assert stability_of("core.swarm.auction.bid") is Stability.BETA
            assert stability_of("core.swarm.other") is Stability.EXPERIMENTAL
        finally:
            stability_of.__globals__["PACKAGE_STABILITY"] = original

    def test_the_facade_is_stable(self) -> None:
        assert stability_of("baselith") is Stability.STABLE

    def test_an_unclassified_name_gets_the_weakest_promise(self) -> None:
        assert stability_of("core.does_not_exist") is DEFAULT_STABILITY
        assert stability_of("") is DEFAULT_STABILITY


class TestSymbolMarkers:
    """A single symbol can carry a tier of its own."""

    @pytest.mark.parametrize(
        ("decorator", "expected"),
        [
            (stable, Stability.STABLE),
            (beta, Stability.BETA),
            (experimental, Stability.EXPERIMENTAL),
        ],
    )
    def test_marker_attaches_the_tier(self, decorator, expected) -> None:  # type: ignore[no-untyped-def]
        def thing() -> None: ...

        assert getattr(decorator(thing), STABILITY_ATTRIBUTE) is expected

    def test_marker_returns_the_same_object(self) -> None:
        """Marking must not wrap: identity, signature and behaviour survive."""

        def thing(x: int) -> int:
            return x + 1

        assert stable(thing) is thing
        assert thing(1) == 2

    def test_marking_an_unmarkable_object_is_not_an_error(self) -> None:
        """The marker is documentation; it never breaks the symbol."""
        assert experimental(42) == 42

    def test_symbol_tier_falls_back_to_the_module(self) -> None:
        def thing() -> None: ...

        thing.__module__ = "core.swarm.auction"
        assert symbol_stability(thing) is Stability.EXPERIMENTAL

    def test_symbol_marker_overrides_its_package(self) -> None:
        @stable
        def thing() -> None: ...

        thing.__module__ = "core.swarm"
        assert symbol_stability(thing) is Stability.STABLE


class TestDeprecated:
    """The announcement half of the deprecation process."""

    def test_function_warns_and_still_works(self) -> None:
        @deprecated(since="0.37", removed_in="1.0", alternative="new_api")
        def old_api(x: int) -> int:
            return x * 2

        with pytest.warns(DeprecationWarning) as caught:
            assert old_api(21) == 42

        message = str(caught[0].message)
        assert "old_api is deprecated since 0.37" in message
        assert "will be removed in 1.0" in message
        assert "use new_api instead" in message

    def test_warning_points_at_the_caller(self) -> None:
        """stacklevel=2, so the warning names the offending call site."""

        @deprecated(since="0.37", removed_in="1.0")
        def old_api() -> None: ...

        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            old_api()

        assert caught[0].filename == __file__

    def test_function_metadata_is_preserved(self) -> None:
        @deprecated(since="0.37", removed_in="1.0")
        def old_api(x: int) -> int:
            """Add one."""
            return x + 1

        assert old_api.__name__ == "old_api"
        assert "Add one." in (old_api.__doc__ or "")

    def test_the_deprecation_lands_in_the_docstring(self) -> None:
        """``help()`` and the docs build show it without reading the source."""

        @deprecated(since="0.37", removed_in="1.0")
        def old_api() -> None:
            """Old thing."""

        assert ".. deprecated::" in (old_api.__doc__ or "")

    def test_class_warns_on_construction(self) -> None:
        @deprecated(since="0.37", removed_in="1.0", alternative="NewThing")
        class OldThing:
            def __init__(self, value: int) -> None:
                self.value = value

        with pytest.warns(DeprecationWarning, match="OldThing is deprecated"):
            instance = OldThing(5)

        assert instance.value == 5

    def test_no_alternative_omits_the_clause(self) -> None:
        @deprecated(since="0.37", removed_in="1.0")
        def old_api() -> None: ...

        with pytest.warns(DeprecationWarning) as caught:
            old_api()

        assert "; use " not in str(caught[0].message)

    def test_deprecated_symbols_report_their_tier(self) -> None:
        @deprecated(since="0.37", removed_in="1.0")
        def old_api() -> None: ...

        assert symbol_stability(old_api) is Stability.DEPRECATED


class TestTableShape:
    """Properties the table has to keep, whatever is in it."""

    def test_every_value_is_a_tier(self) -> None:
        assert all(isinstance(t, Stability) for t in PACKAGE_STABILITY.values())

    def test_every_key_is_a_dotted_package_name(self) -> None:
        assert all(
            name == "baselith" or name.startswith("core.") for name in PACKAGE_STABILITY
        )

    def test_the_stable_tier_stays_small_enough_to_mean_something(self) -> None:
        """A promise everything carries is a promise nothing carries."""
        stable_count = sum(
            1 for t in PACKAGE_STABILITY.values() if t is Stability.STABLE
        )

        assert stable_count <= len(PACKAGE_STABILITY) // 4

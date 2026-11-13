"""Every spelling of "the configured model" must share one loaded instance."""

from __future__ import annotations

from baselith.core.nlp._named_cache import NamedModelCache


class TestNamedModelCache:
    def test_default_none_and_explicit_name_share_one_instance(self) -> None:
        built: list[str] = []

        def build(name: str) -> object:
            built.append(name)
            return object()

        get_model = NamedModelCache(lambda: "configured", build)

        assert get_model() is get_model(None) is get_model("configured")
        assert built == ["configured"]

    def test_a_different_name_is_a_different_instance(self) -> None:
        get_model = NamedModelCache(lambda: "configured", lambda name: object())

        assert get_model("other") is not get_model()
        assert get_model("other") is get_model("other")

    def test_the_default_is_read_on_each_call(self) -> None:
        current = ["a"]
        get_model = NamedModelCache(lambda: current[0], lambda name: name.upper())

        assert get_model() == "A"
        current[0] = "b"
        assert get_model() == "B"

    def test_cache_clear_rebuilds(self) -> None:
        get_model = NamedModelCache(lambda: "configured", lambda name: object())
        first = get_model()

        get_model.cache_clear()

        assert get_model() is not first
        assert get_model.cache_info().currsize == 1

    def test_the_accessors_are_keyed_on_the_resolved_name(self) -> None:
        from baselith.core.nlp import models

        assert isinstance(models.get_embedder, NamedModelCache)
        assert isinstance(models.get_reranker, NamedModelCache)

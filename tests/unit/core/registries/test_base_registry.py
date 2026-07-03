"""Tests for the generic BaseRegistry."""

import pytest

from core.exceptions import DuplicateRegistrationError, ItemNotFoundError
from core.registries import BaseRegistry


class _Item:
    def __init__(self, name: str, tag: str = ""):
        self.name = name
        self.tag = tag


class TestBaseRegistry:
    def test_register_by_name_attribute(self):
        reg: BaseRegistry[_Item] = BaseRegistry()
        key = reg.register(_Item("alpha"))
        assert key == "alpha"
        assert reg.get("alpha").name == "alpha"
        assert len(reg) == 1

    def test_register_explicit_name(self):
        reg: BaseRegistry[object] = BaseRegistry()
        reg.register(object(), name="thing")
        assert "thing" in reg

    def test_register_with_key_function(self):
        reg: BaseRegistry[_Item] = BaseRegistry(key=lambda i: i.tag)
        reg.register(_Item("alpha", tag="t1"))
        assert reg.get("t1") is not None

    def test_unkeyable_item_raises(self):
        reg: BaseRegistry[object] = BaseRegistry()
        with pytest.raises(KeyError):
            reg.register(object())

    def test_overwrite_default_true(self):
        reg: BaseRegistry[_Item] = BaseRegistry()
        reg.register(_Item("a", tag="first"))
        reg.register(_Item("a", tag="second"))
        assert reg.get("a").tag == "second"

    def test_overwrite_false_raises(self):
        reg: BaseRegistry[_Item] = BaseRegistry()
        reg.register(_Item("a"))
        with pytest.raises(DuplicateRegistrationError):
            reg.register(_Item("a"), overwrite=False)

    def test_remove(self):
        reg: BaseRegistry[_Item] = BaseRegistry()
        reg.register(_Item("a"))
        assert reg.remove("a") is True
        assert reg.remove("a") is False
        assert reg.get("a") is None

    def test_require_raises_when_missing(self):
        reg: BaseRegistry[_Item] = BaseRegistry()
        with pytest.raises(ItemNotFoundError):
            reg.require("nope")

    def test_list_and_predicate(self):
        reg: BaseRegistry[_Item] = BaseRegistry()
        reg.register(_Item("a", tag="x"))
        reg.register(_Item("b", tag="y"))
        assert len(reg.list()) == 2
        filtered = reg.list(lambda i: i.tag == "x")
        assert len(filtered) == 1 and filtered[0].name == "a"

    def test_names_clear_iter(self):
        reg: BaseRegistry[_Item] = BaseRegistry()
        reg.register(_Item("a"))
        reg.register(_Item("b"))
        assert set(reg.names()) == {"a", "b"}
        assert {i.name for i in reg} == {"a", "b"}
        reg.clear()
        assert len(reg) == 0

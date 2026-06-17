"""Tests for the prompt registry, rendering, and loader."""

from pathlib import Path

import pytest

from core.prompts import (
    InMemoryPromptStore,
    PromptNotFoundError,
    PromptRegistry,
    PromptRenderError,
    PromptVersion,
    compute_checksum,
    find_placeholders,
    load_prompts_from_dir,
    parse_prompt_file,
    render_template,
)
from core.prompts.loader import PromptLoadError


# === Rendering ===
class TestRendering:
    def test_basic_substitution(self):
        assert render_template("Hi {{ name }}", {"name": "Gio"}) == "Hi Gio"

    def test_whitespace_insensitive_placeholder(self):
        assert render_template("a {{x}} {{  y  }}", {"x": "1", "y": "2"}) == "a 1 2"

    def test_strict_missing_raises(self):
        with pytest.raises(PromptRenderError, match="Missing"):
            render_template("{{ a }} {{ b }}", {"a": "1"})

    def test_non_strict_leaves_placeholder(self):
        assert (
            render_template("{{ a }} {{ b }}", {"a": "1"}, strict=False) == "1 {{ b }}"
        )

    def test_no_format_injection(self):
        # str.format would explode on {} or access attributes; the renderer must
        # treat braces literally and never evaluate.
        out = render_template("balance={{ amt }} {literal} {0}", {"amt": "5"})
        assert out == "balance=5 {literal} {0}"

    def test_value_with_braces_is_literal(self):
        # A variable value containing {{...}} must not be re-expanded.
        assert render_template("{{ a }}", {"a": "{{ b }}"}) == "{{ b }}"

    def test_find_placeholders(self):
        assert find_placeholders("{{ a }} {{ b }} {{ a }}") == ["a", "b"]


# === Types ===
class TestTypes:
    def test_checksum_auto_computed(self):
        pv = PromptVersion(name="p", template="hello")
        assert pv.checksum == compute_checksum("hello")

    def test_key(self):
        assert PromptVersion(name="p", version="3", template="x").key() == "p@3"

    def test_span_attributes(self):
        reg = PromptRegistry()
        reg.register("p", "hi {{ n }}", version="7")
        rp = reg.render("p", {"n": "x"}, version="7")
        attrs = rp.span_attributes()
        assert attrs["prompt.name"] == "p"
        assert attrs["prompt.version"] == "7"
        assert "prompt.checksum" in attrs


# === Registry ===
class TestRegistry:
    def _reg(self):
        r = PromptRegistry()
        r.register("greet", "Hello {{ name }}", version="1", labels={"production"})
        r.register("greet", "Hi {{ name }}!", version="2")
        return r

    def test_get_latest(self):
        assert self._reg().get("greet").version == "2"

    def test_get_by_version(self):
        assert self._reg().get("greet", version="1").template == "Hello {{ name }}"

    def test_get_by_label(self):
        assert self._reg().get("greet", label="production").version == "1"

    def test_get_unknown_name_raises(self):
        with pytest.raises(PromptNotFoundError):
            PromptRegistry().get("nope")

    def test_get_unknown_version_raises(self):
        with pytest.raises(PromptNotFoundError):
            self._reg().get("greet", version="99")

    def test_get_unknown_label_raises(self):
        with pytest.raises(PromptNotFoundError):
            self._reg().get("greet", label="canary")

    def test_render_resolves_then_renders(self):
        assert (
            self._reg().render("greet", {"name": "Gio"}, version="1").text
            == "Hello Gio"
        )

    def test_promote_moves_label(self):
        r = self._reg()
        assert r.get("greet", label="production").version == "1"
        r.promote("greet", "2", "production")
        assert r.get("greet", label="production").version == "2"

    def test_promote_unknown_version_raises(self):
        with pytest.raises(PromptNotFoundError):
            self._reg().promote("greet", "99", "production")

    def test_list_versions_order(self):
        versions = [v.version for v in self._reg().list_versions("greet")]
        assert versions == ["1", "2"]

    def test_register_is_idempotent_on_version(self):
        r = self._reg()
        r.register("greet", "Hello {{ name }} updated", version="1")
        # Same version key updates in place; still two versions total.
        assert len(r.list_versions("greet")) == 2
        assert r.get("greet", version="1").template == "Hello {{ name }} updated"


# === A/B selection ===
class TestVariantSelection:
    def _reg(self):
        r = PromptRegistry()
        r.register("exp", "A {{ x }}", version="a")
        r.register("exp", "B {{ x }}", version="b")
        return r

    def test_deterministic_per_subject(self):
        r = self._reg()
        first = r.select_variant("exp", "user-1", {"a": 50, "b": 50}).version
        for _ in range(5):
            assert (
                r.select_variant("exp", "user-1", {"a": 50, "b": 50}).version == first
            )

    def test_zero_weight_excluded(self):
        r = self._reg()
        for subj in ["u1", "u2", "u3", "u4", "u5"]:
            assert r.select_variant("exp", subj, {"a": 100, "b": 0}).version == "a"

    def test_no_weights_raises(self):
        with pytest.raises(PromptNotFoundError):
            self._reg().select_variant("exp", "u", {"a": 0, "b": 0})

    def test_distribution_roughly_splits(self):
        r = self._reg()
        counts = {"a": 0, "b": 0}
        for i in range(400):
            counts[
                r.select_variant("exp", f"user-{i}", {"a": 50, "b": 50}).version
            ] += 1
        # Both variants should get a meaningful share (not all-or-nothing).
        assert counts["a"] > 120 and counts["b"] > 120


# === Loader ===
class TestLoader:
    def _write(self, dir_, name, content):
        p = Path(dir_) / name
        p.write_text(content, encoding="utf-8")
        return p

    def test_parse_front_matter(self, tmp_path):
        p = self._write(
            tmp_path,
            "greet.md",
            "---\nname: greet\nversion: '2'\nlabels: [production]\nvariables: [name]\n---\nHello {{ name }}\n",
        )
        pv = parse_prompt_file(p)
        assert pv.name == "greet"
        assert pv.version == "2"
        assert pv.labels == {"production"}
        assert pv.template == "Hello {{ name }}"  # trailing newline stripped

    def test_missing_front_matter_raises(self, tmp_path):
        p = self._write(tmp_path, "x.md", "just a body")
        with pytest.raises(PromptLoadError):
            parse_prompt_file(p)

    def test_name_defaults_to_filename(self, tmp_path):
        p = self._write(tmp_path, "welcome.md", "---\nversion: '1'\n---\nWelcome")
        assert parse_prompt_file(p).name == "welcome"

    def test_load_dir_skips_bad_files(self, tmp_path):
        self._write(tmp_path, "ok.md", "---\nname: ok\n---\nOK body")
        self._write(tmp_path, "bad.md", "no front matter")
        r = PromptRegistry()
        loaded = load_prompts_from_dir(r, tmp_path)
        assert len(loaded) == 1
        assert r.get("ok").template == "OK body"

    def test_load_missing_dir_returns_empty(self, tmp_path):
        assert load_prompts_from_dir(PromptRegistry(), tmp_path / "nope") == []


# === Store ===
def test_inmemory_store_label_resolution():
    store = InMemoryPromptStore()
    store.put(PromptVersion(name="p", version="1", template="a", labels={"prod"}))
    assert store.resolve_label("p", "prod") == "1"
    store.set_label("p", "prod", "2")
    assert store.resolve_label("p", "prod") == "2"

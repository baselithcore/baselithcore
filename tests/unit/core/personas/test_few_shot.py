"""Unit tests for ``core.personas.few_shot``."""

from __future__ import annotations

from pathlib import Path

import pytest

from core.personas.few_shot import (
    FewShotExample,
    FewShotLibrary,
    FewShotLoadError,
    load_library,
)


class TestFewShotLibrary:
    def test_add_and_select(self) -> None:
        lib = FewShotLibrary()
        lib.add("translation", FewShotExample(input="hello", output="ciao"))
        result = lib.select("translation")
        assert len(result) == 1
        assert result[0].output == "ciao"

    def test_select_respects_limit(self) -> None:
        lib = FewShotLibrary()
        for i in range(5):
            lib.add("x", FewShotExample(input=str(i), output=str(i)))
        assert len(lib.select("x", limit=2)) == 2

    def test_select_filters_by_tags(self) -> None:
        lib = FewShotLibrary()
        lib.add("x", FewShotExample(input="a", output="A", tags=("formal",)))
        lib.add("x", FewShotExample(input="b", output="B", tags=("casual",)))
        result = lib.select("x", tags=["formal"])
        assert len(result) == 1
        assert result[0].input == "a"

    def test_select_unknown_task_returns_empty(self) -> None:
        lib = FewShotLibrary()
        assert lib.select("nope") == []

    def test_render_produces_markdown_block(self) -> None:
        lib = FewShotLibrary()
        lib.add(
            "summarize",
            FewShotExample(
                input="long text",
                output="short summary",
                rationale="extracted key sentence",
            ),
        )
        rendered = lib.render("summarize")
        assert "### Example 1" in rendered
        assert "long text" in rendered
        assert "short summary" in rendered
        assert "extracted key sentence" in rendered

    def test_render_empty_returns_empty_string(self) -> None:
        assert FewShotLibrary().render("missing") == ""

    def test_task_types_sorted(self) -> None:
        lib = FewShotLibrary()
        lib.add("zeta", FewShotExample(input="x", output="y"))
        lib.add("alpha", FewShotExample(input="x", output="y"))
        assert lib.task_types() == ["alpha", "zeta"]

    def test_empty_task_type_rejected(self) -> None:
        lib = FewShotLibrary()
        with pytest.raises(ValueError):
            lib.add("", FewShotExample(input="x", output="y"))

    def test_zero_limit_rejected(self) -> None:
        lib = FewShotLibrary()
        with pytest.raises(ValueError):
            lib.select("x", limit=0)


class TestLoadLibrary:
    def test_load_from_yaml(self, tmp_path: Path) -> None:
        p = tmp_path / "examples.yaml"
        p.write_text(
            """
translation:
  - input: hello
    output: ciao
  - input: goodbye
    output: arrivederci
summarize:
  - input: long article
    output: short summary
    rationale: extracted the lead sentence
    tags: [news]
""".strip(),
            encoding="utf-8",
        )
        lib = load_library(p)
        assert lib.task_types() == ["summarize", "translation"]
        translation = lib.select("translation")
        assert len(translation) == 2
        summary = lib.select("summarize")
        assert summary[0].rationale is not None
        assert "news" in summary[0].tags

    def test_load_from_json(self, tmp_path: Path) -> None:
        p = tmp_path / "examples.json"
        p.write_text('{"x": [{"input": "a", "output": "b"}]}', encoding="utf-8")
        lib = load_library(p)
        assert lib.select("x")[0].output == "b"

    def test_missing_file_raises(self, tmp_path: Path) -> None:
        with pytest.raises(FewShotLoadError):
            load_library(tmp_path / "nope.yaml")

    def test_top_level_must_be_mapping(self, tmp_path: Path) -> None:
        p = tmp_path / "bad.yaml"
        p.write_text("- just\n- a\n- list\n", encoding="utf-8")
        with pytest.raises(FewShotLoadError):
            load_library(p)

    def test_examples_must_be_list(self, tmp_path: Path) -> None:
        p = tmp_path / "bad.yaml"
        p.write_text("task: not_a_list\n", encoding="utf-8")
        with pytest.raises(FewShotLoadError):
            load_library(p)

    def test_missing_input_field_rejected(self, tmp_path: Path) -> None:
        p = tmp_path / "bad.yaml"
        p.write_text(
            """
x:
  - output: no input here
""".strip(),
            encoding="utf-8",
        )
        with pytest.raises(FewShotLoadError):
            load_library(p)

    def test_invalid_tags_rejected(self, tmp_path: Path) -> None:
        p = tmp_path / "bad.yaml"
        p.write_text(
            """
x:
  - input: a
    output: b
    tags: not_a_list
""".strip(),
            encoding="utf-8",
        )
        with pytest.raises(FewShotLoadError):
            load_library(p)

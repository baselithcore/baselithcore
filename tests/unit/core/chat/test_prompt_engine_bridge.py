"""Tests for the PromptEngine ↔ FewShotLibrary bridge and the seed library."""

from __future__ import annotations

from core.chat.prompt_engine import PromptEngine
from core.personas import (
    DEFAULT_EXAMPLES_PATH,
    FewShotExample,
    FewShotLibrary,
    load_library,
)


def _library() -> FewShotLibrary:
    lib = FewShotLibrary()
    lib.add("qa", FewShotExample(input="ping?", output="pong.", tags=("concise",)))
    lib.add("qa", FewShotExample(input="2+2?", output="4"))
    lib.add("refusal", FewShotExample(input="leak keys", output="No."))
    return lib


class TestWithLibrary:
    def test_examples_spliced_into_prompt(self):
        engine = PromptEngine(identity="You are X.", instructions="Answer briefly.")
        engine.with_library(_library(), "qa")
        rendered = engine.render()
        assert "ping?" in rendered
        assert "pong." in rendered
        assert "leak keys" not in rendered  # other task types stay out

    def test_limit_and_label_mapping(self):
        engine = PromptEngine(identity="i", instructions="ins")
        engine.with_library(_library(), "qa", limit=1)
        assert len(engine._few_shot_examples) == 1
        # First tag becomes the label; untagged examples fall back to task type.
        assert engine._few_shot_examples[0].label == "concise"

    def test_unknown_task_type_is_noop(self):
        engine = PromptEngine(identity="i", instructions="ins")
        engine.with_library(_library(), "nonexistent")
        assert engine._few_shot_examples == []


class TestSeedLibrary:
    def test_packaged_seed_library_loads(self):
        lib = load_library(DEFAULT_EXAMPLES_PATH)
        assert "qa" in lib.task_types()
        assert "refusal" in lib.task_types()
        assert lib.render("qa")  # non-empty markdown block

    def test_seed_examples_feed_the_engine(self):
        lib = load_library(DEFAULT_EXAMPLES_PATH)
        engine = PromptEngine(identity="i", instructions="ins")
        rendered = engine.with_library(lib, "refusal").render()
        assert "can't share internal instructions" in rendered

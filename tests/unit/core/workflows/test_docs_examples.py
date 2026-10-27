"""The workflow written in the catalog docs must load, validate and run."""

import re
from pathlib import Path

import pytest

from baselith.core.workflows.executor import ExecutionStatus, WorkflowExecutor
from baselith.core.workflows.serialization import load_definition

pytestmark = [pytest.mark.unit]

DOC = (
    Path(__file__).resolve().parents[4]
    / "mkdocs-site"
    / "docs"
    / "core-modules"
    / "workflow-catalog.md"
)


def _example() -> str:
    text = DOC.read_text(encoding="utf-8")
    for block in re.findall(r"```yaml\n(.*?)```", text, re.DOTALL):
        if "id: support-answer" in block:
            return block
    raise AssertionError("the support-answer example is missing from the docs")


def test_documented_workflow_validates():
    workflow = load_definition(_example())
    assert workflow.id == "support-answer"
    assert workflow.webhook_trigger is True
    assert workflow.metadata["mcp"]["expose"] is True
    assert workflow.validate() == []


async def test_documented_workflow_runs_end_to_end():
    seen = {}

    async def retriever(query, *, top_k, collection):
        seen["retrieve"] = (query, top_k)
        return [
            {
                "id": "1",
                "content": "Open Settings, then Security.",
                "score": 0.9,
                "metadata": {},
            },
            {
                "id": "2",
                "content": "Choose Reset password.",
                "score": 0.5,
                "metadata": {},
            },
        ]

    class Llm:
        async def generate_response(self, prompt, **kwargs):
            seen["prompt"] = prompt
            seen["system"] = kwargs["system_prompt"]
            return "Go to Settings > Security > Reset password."

    result = await WorkflowExecutor(
        services={"retriever": retriever, "llm": Llm()}
    ).execute(load_definition(_example()), {"question": "How do I reset my password?"})
    assert result.status is ExecutionStatus.COMPLETED, result.error
    assert result.output == "Go to Settings > Security > Reset password."
    assert seen["retrieve"] == ("How do I reset my password?", 4)
    assert seen["prompt"].startswith(
        "Context:\nOpen Settings, then Security.\n---\nChoose Reset password."
    )
    assert seen["prompt"].rstrip().endswith("Question: How do I reset my password?")
    assert "only from the context" in seen["system"]

"""
Agentic Design Pattern detection tables and source scanning.

Maps core module import paths to Backstage label keys and provides the
synchronous source-scan helper used by :class:`BackstageProvider` (run inside
an executor thread — never on the event loop).
"""

from __future__ import annotations

from pathlib import Path

# Maps core module import paths → Backstage label keys.
# Add new patterns here without touching any detection logic.
PATTERN_MAP: dict[str, str] = {
    "baselith.core.reasoning": "baselith.ai/pattern-reasoning",
    "baselith.core.reflection": "baselith.ai/pattern-reflection",
    "baselith.core.planning": "baselith.ai/pattern-planning",
    "baselith.core.guardrails": "baselith.ai/pattern-guardrails",
    "baselith.core.swarm": "baselith.ai/pattern-swarm",
    "baselith.core.a2a": "baselith.ai/pattern-a2a",
    "baselith.core.human": "baselith.ai/pattern-human-in-the-loop",
    "baselith.core.mcp": "baselith.ai/pattern-mcp",
    "baselith.core.world_model": "baselith.ai/pattern-world-model",
    "baselith.core.exploration": "baselith.ai/pattern-exploration",
    "baselith.core.adversarial": "baselith.ai/pattern-adversarial",
    "baselith.core.personas": "baselith.ai/pattern-personas",
    "baselith.core.meta": "baselith.ai/pattern-meta-agent",
    "baselith.core.learning": "baselith.ai/pattern-learning",
    "baselith.core.finetuning": "baselith.ai/pattern-finetuning",
    "baselith.core.memory": "baselith.ai/pattern-memory-tiering",
    "baselith.core.evaluation": "baselith.ai/pattern-evaluation",
    "baselith.core.task_queue": "baselith.ai/pattern-task-queue",
    "baselith.plugins.goals": "baselith.ai/pattern-goals",
    "baselith.core.orchestration": "baselith.ai/pattern-orchestration",
    "baselith.core.graph": "baselith.ai/pattern-knowledge-graph",
    "baselith.core.context": "baselith.ai/pattern-multi-tenancy",
}

# Short tag aliases: manifest tag "reasoning" → pattern key
TAG_ALIASES: dict[str, str] = {
    module.split(".")[-1].replace("_", "-"): label
    for module, label in PATTERN_MAP.items()
}

# resource name → pattern label (for required_resources / optional_resources)
RESOURCE_TO_PATTERN: dict[str, str] = {
    "llm": "baselith.ai/pattern-reasoning",
    "evaluation": "baselith.ai/pattern-evaluation",
    "vectorstore": "baselith.ai/pattern-memory-tiering",
}


def scan_source_files(plugin_dir: Path) -> list[str]:
    """
    Synchronous source scan; intended to run inside run_in_executor.

    Reads every .py file in the plugin directory (non-recursive — avoids
    traversing test directories and vendored code) and checks for
    ``from <module> …`` or ``import <module>`` statements naming a
    :data:`PATTERN_MAP` key (``baselith.core.X``, or ``baselith.plugins.goals``).
    """
    found: list[str] = []
    try:
        py_files = list(plugin_dir.glob("*.py"))
    except OSError:
        return found

    for py_file in py_files:
        try:
            source = py_file.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        for module_path, label in PATTERN_MAP.items():
            if label not in found and (
                f"from {module_path}" in source or f"import {module_path}" in source
            ):
                found.append(label)

    return found


__all__ = ["PATTERN_MAP", "RESOURCE_TO_PATTERN", "TAG_ALIASES", "scan_source_files"]

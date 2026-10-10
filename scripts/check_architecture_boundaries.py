"""Enforce Sacred Core architectural boundaries.

This checker is intentionally conservative:
- it freezes the current set of legacy domain-specific modules that still live in
  ``baselith/core/`` during the migration period;
- it blocks new ``core -> plugins`` imports except for explicit compatibility
  shims that are already present and scheduled for removal.

The goal is to stop architectural drift immediately without forcing the full
migration to happen in a single step.
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path
from typing import Iterable

REPO_ROOT = Path(__file__).resolve().parents[1]

FROZEN_CORE_PREFIXES = (
    "baselith/core/agents/",
    "baselith/core/doc_sources/",
    "baselith/core/goals/",
    "baselith/core/routers/",
    "baselith/core/scraper/",
)

LEGACY_CORE_FILE_ALLOWLIST = frozenset(
    {
        "baselith/core/agents/__init__.py",
        "baselith/core/agents/browser_agent.py",
        "baselith/core/agents/browser_tools.py",
        "baselith/core/agents/browser_types.py",
        "baselith/core/agents/coding/__init__.py",
        "baselith/core/agents/coding/agent.py",
        "baselith/core/agents/coding/prompts.py",
        "baselith/core/agents/coding/types.py",
        "baselith/core/agents/coding_tools.py",
        "baselith/core/doc_sources/__init__.py",
        "baselith/core/doc_sources/filesystem.py",
        "baselith/core/doc_sources/labels.py",
        "baselith/core/doc_sources/models.py",
        "baselith/core/doc_sources/ocr_backends.py",
        "baselith/core/doc_sources/readers.py",
        "baselith/core/doc_sources/registry.py",
        "baselith/core/doc_sources/utils.py",
        "baselith/core/doc_sources/web.py",
        "baselith/core/doc_sources/web_constants.py",
        "baselith/core/doc_sources/web_parser.py",
        "baselith/core/goals/__init__.py",
        "baselith/core/goals/tracker.py",
        "baselith/core/routers/__init__.py",
        "baselith/core/routers/admin.py",
        "baselith/core/routers/chat.py",
        "baselith/core/routers/console.py",
        "baselith/core/routers/feedback.py",
        "baselith/core/routers/index.py",
        "baselith/core/routers/metrics.py",
        "baselith/core/routers/status.py",
        "baselith/core/routers/tenant.py",
        "baselith/core/scraper/__init__.py",
        "baselith/core/scraper/crawler.py",
        "baselith/core/scraper/extractors/__init__.py",
        "baselith/core/scraper/extractors/base.py",
        "baselith/core/scraper/extractors/css_selector.py",
        "baselith/core/scraper/extractors/images.py",
        "baselith/core/scraper/extractors/links.py",
        "baselith/core/scraper/extractors/metadata.py",
        "baselith/core/scraper/extractors/schema_org.py",
        "baselith/core/scraper/extractors/text.py",
        "baselith/core/scraper/fetchers/__init__.py",
        "baselith/core/scraper/fetchers/base.py",
        "baselith/core/scraper/fetchers/httpx_fetcher.py",
        "baselith/core/scraper/fetchers/playwright_fetcher.py",
        "baselith/core/scraper/middleware/__init__.py",
        "baselith/core/scraper/middleware/base.py",
        "baselith/core/scraper/middleware/cache.py",
        "baselith/core/scraper/middleware/logging.py",
        "baselith/core/scraper/middleware/rate_limiter.py",
        "baselith/core/scraper/middleware/retry.py",
        "baselith/core/scraper/models.py",
        "baselith/core/scraper/protocols.py",
        "baselith/core/scraper/scraper.py",
        "baselith/core/scraper/storage/__init__.py",
        "baselith/core/scraper/storage/base.py",
        "baselith/core/scraper/storage/filesystem.py",
        "baselith/core/scraper/storage/memory.py",
        "baselith/core/scraper/tools.py",
        "baselith/core/scraper/utils.py",
    }
)

CORE_TO_PLUGIN_IMPORT_ALLOWLIST = {
    "baselith/core/agents/browser_agent.py": {"baselith.plugins.browser_agent.agent"},
    "baselith/core/agents/browser_tools.py": {"baselith.plugins.browser_agent.tools"},
    "baselith/core/agents/browser_types.py": {"baselith.plugins.browser_agent.types"},
    "baselith/core/agents/coding/__init__.py": {"baselith.plugins.coding_agent"},
    "baselith/core/agents/coding/agent.py": {
        "baselith.plugins.coding_agent.agent",
        "baselith.plugins.coding_agent.types",
    },
    "baselith/core/agents/coding/prompts.py": {"baselith.plugins.coding_agent.prompts"},
    "baselith/core/agents/coding/types.py": {"baselith.plugins.coding_agent.types"},
    "baselith/core/agents/coding_tools.py": {"baselith.plugins.coding_agent.tools"},
    "baselith/core/doc_sources/__init__.py": {"baselith.plugins.document_sources"},
    "baselith/core/doc_sources/filesystem.py": {
        "baselith.plugins.document_sources.filesystem"
    },
    "baselith/core/doc_sources/labels.py": {"baselith.plugins.document_sources.labels"},
    "baselith/core/doc_sources/models.py": {"baselith.plugins.document_sources.models"},
    "baselith/core/doc_sources/ocr_backends.py": {
        "baselith.plugins.document_sources.ocr_backends"
    },
    "baselith/core/doc_sources/readers.py": {
        "baselith.plugins.document_sources.readers"
    },
    "baselith/core/doc_sources/registry.py": {
        "baselith.plugins.document_sources.registry"
    },
    "baselith/core/doc_sources/utils.py": {"baselith.plugins.document_sources.utils"},
    "baselith/core/doc_sources/web.py": {"baselith.plugins.document_sources.web"},
    "baselith/core/doc_sources/web_constants.py": {
        "baselith.plugins.document_sources.web_constants"
    },
    "baselith/core/doc_sources/web_parser.py": {
        "baselith.plugins.document_sources.web_parser"
    },
    "baselith/core/goals/__init__.py": {"baselith.plugins.goals"},
    "baselith/core/goals/tracker.py": {"baselith.plugins.goals.tracker"},
    "baselith/core/routers/admin.py": {"baselith.plugins.api_routers.admin"},
    "baselith/core/routers/chat.py": {"baselith.plugins.api_routers.chat"},
    "baselith/core/routers/console.py": {"baselith.plugins.api_routers.console"},
    "baselith/core/routers/feedback.py": {"baselith.plugins.api_routers.feedback"},
    "baselith/core/routers/index.py": {"baselith.plugins.api_routers.index"},
    "baselith/core/routers/metrics.py": {"baselith.plugins.api_routers.metrics"},
    "baselith/core/routers/status.py": {"baselith.plugins.api_routers.status"},
    "baselith/core/routers/tenant.py": {"baselith.plugins.api_routers.tenant"},
}

CORE_TO_PLUGIN_IMPORT_PREFIX_ALLOWLIST = {
    "baselith/core/scraper/": {"baselith.plugins.web_scraper"},
}


def iter_python_files(root: Path) -> Iterable[Path]:
    """Yield all Python files under the repository root, excluding hidden caches."""
    for path in root.rglob("*.py"):
        rel = path.relative_to(root)
        if any(part.startswith(".") for part in rel.parts):
            continue
        if "__pycache__" in rel.parts:
            continue
        yield path


def module_allowed(
    relative_path: str, module_name: str, allowlist: dict[str, set[str]]
) -> bool:
    """Return True when a ``core -> plugins`` import is explicitly grandfathered."""
    allowed_modules = allowlist.get(relative_path, set())
    return any(
        module_name == allowed_module or module_name.startswith(f"{allowed_module}.")
        for allowed_module in allowed_modules
    )


def module_allowed_by_prefix(
    relative_path: str,
    module_name: str,
    allowlist: dict[str, set[str]],
) -> bool:
    """Return True when a shimmed path prefix is allowed to target a plugin prefix."""
    for path_prefix, allowed_modules in allowlist.items():
        if not relative_path.startswith(path_prefix):
            continue
        if any(
            module_name == allowed_module
            or module_name.startswith(f"{allowed_module}.")
            for allowed_module in allowed_modules
        ):
            return True
    return False


def imported_modules(path: Path) -> list[str]:
    """Extract imported module names from a Python source file."""
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    modules: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            modules.append(node.module)
    return modules


def check_boundaries(
    root: Path,
    *,
    frozen_prefixes: tuple[str, ...] = FROZEN_CORE_PREFIXES,
    legacy_allowlist: frozenset[str] = LEGACY_CORE_FILE_ALLOWLIST,
    core_to_plugin_allowlist: dict[str, set[str]] = CORE_TO_PLUGIN_IMPORT_ALLOWLIST,
) -> list[str]:
    """Validate architecture boundaries and return all violations."""
    violations: list[str] = []

    for path in iter_python_files(root):
        relative_path = path.relative_to(root).as_posix()

        if relative_path.startswith("baselith/core/"):
            if (
                relative_path.startswith(frozen_prefixes)
                and relative_path not in legacy_allowlist
            ):
                violations.append(
                    f"{relative_path}: new legacy/domain-specific module added under frozen core path"
                )

            for module_name in imported_modules(path):
                if not module_name.startswith("baselith.plugins"):
                    continue
                if module_allowed(relative_path, module_name, core_to_plugin_allowlist):
                    continue
                if module_allowed_by_prefix(
                    relative_path,
                    module_name,
                    CORE_TO_PLUGIN_IMPORT_PREFIX_ALLOWLIST,
                ):
                    continue
                violations.append(
                    f"{relative_path}: forbidden core->plugins import '{module_name}'"
                )

    return violations


def main() -> int:
    """CLI entrypoint."""
    violations = check_boundaries(REPO_ROOT)
    if violations:
        print("Architecture boundary violations detected:", file=sys.stderr)
        for violation in violations:
            print(f" - {violation}", file=sys.stderr)
        return 1

    print("Architecture boundaries OK.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

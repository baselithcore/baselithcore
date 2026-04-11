"""Enforce Sacred Core architectural boundaries.

This checker is intentionally conservative:
- it freezes the current set of legacy domain-specific modules that still live in
  ``core/`` during the migration period;
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
    "core/agents/",
    "core/doc_sources/",
    "core/goals/",
    "core/routers/",
    "core/scraper/",
)

LEGACY_CORE_FILE_ALLOWLIST = frozenset(
    {
        "core/agents/__init__.py",
        "core/agents/browser_agent.py",
        "core/agents/browser_tools.py",
        "core/agents/browser_types.py",
        "core/agents/coding/__init__.py",
        "core/agents/coding/agent.py",
        "core/agents/coding/prompts.py",
        "core/agents/coding/types.py",
        "core/agents/coding_tools.py",
        "core/doc_sources/__init__.py",
        "core/doc_sources/filesystem.py",
        "core/doc_sources/labels.py",
        "core/doc_sources/models.py",
        "core/doc_sources/ocr_backends.py",
        "core/doc_sources/readers.py",
        "core/doc_sources/registry.py",
        "core/doc_sources/utils.py",
        "core/doc_sources/web.py",
        "core/doc_sources/web_constants.py",
        "core/doc_sources/web_parser.py",
        "core/goals/__init__.py",
        "core/goals/tracker.py",
        "core/routers/__init__.py",
        "core/routers/admin.py",
        "core/routers/chat.py",
        "core/routers/console.py",
        "core/routers/feedback.py",
        "core/routers/index.py",
        "core/routers/metrics.py",
        "core/routers/status.py",
        "core/routers/tenant.py",
        "core/scraper/__init__.py",
        "core/scraper/crawler.py",
        "core/scraper/extractors/__init__.py",
        "core/scraper/extractors/base.py",
        "core/scraper/extractors/css_selector.py",
        "core/scraper/extractors/images.py",
        "core/scraper/extractors/links.py",
        "core/scraper/extractors/metadata.py",
        "core/scraper/extractors/schema_org.py",
        "core/scraper/extractors/text.py",
        "core/scraper/fetchers/__init__.py",
        "core/scraper/fetchers/base.py",
        "core/scraper/fetchers/httpx_fetcher.py",
        "core/scraper/fetchers/playwright_fetcher.py",
        "core/scraper/middleware/__init__.py",
        "core/scraper/middleware/base.py",
        "core/scraper/middleware/cache.py",
        "core/scraper/middleware/logging.py",
        "core/scraper/middleware/rate_limiter.py",
        "core/scraper/middleware/retry.py",
        "core/scraper/models.py",
        "core/scraper/protocols.py",
        "core/scraper/scraper.py",
        "core/scraper/storage/__init__.py",
        "core/scraper/storage/base.py",
        "core/scraper/storage/filesystem.py",
        "core/scraper/storage/memory.py",
        "core/scraper/tools.py",
        "core/scraper/utils.py",
    }
)

CORE_TO_PLUGIN_IMPORT_ALLOWLIST = {
    "core/agents/browser_agent.py": {"plugins.browser_agent.agent"},
    "core/agents/browser_tools.py": {"plugins.browser_agent.tools"},
    "core/agents/browser_types.py": {"plugins.browser_agent.types"},
    "core/agents/coding/__init__.py": {"plugins.coding_agent"},
    "core/agents/coding/agent.py": {
        "plugins.coding_agent.agent",
        "plugins.coding_agent.types",
    },
    "core/agents/coding/prompts.py": {"plugins.coding_agent.prompts"},
    "core/agents/coding/types.py": {"plugins.coding_agent.types"},
    "core/agents/coding_tools.py": {"plugins.coding_agent.tools"},
    "core/doc_sources/__init__.py": {"plugins.document_sources"},
    "core/doc_sources/filesystem.py": {"plugins.document_sources.filesystem"},
    "core/doc_sources/labels.py": {"plugins.document_sources.labels"},
    "core/doc_sources/models.py": {"plugins.document_sources.models"},
    "core/doc_sources/ocr_backends.py": {"plugins.document_sources.ocr_backends"},
    "core/doc_sources/readers.py": {"plugins.document_sources.readers"},
    "core/doc_sources/registry.py": {"plugins.document_sources.registry"},
    "core/doc_sources/utils.py": {"plugins.document_sources.utils"},
    "core/doc_sources/web.py": {"plugins.document_sources.web"},
    "core/doc_sources/web_constants.py": {"plugins.document_sources.web_constants"},
    "core/doc_sources/web_parser.py": {"plugins.document_sources.web_parser"},
    "core/goals/__init__.py": {"plugins.goals"},
    "core/goals/tracker.py": {"plugins.goals.tracker"},
    "core/routers/admin.py": {"plugins.api_routers.admin"},
    "core/routers/chat.py": {"plugins.api_routers.chat"},
    "core/routers/console.py": {"plugins.api_routers.console"},
    "core/routers/feedback.py": {"plugins.api_routers.feedback"},
    "core/routers/index.py": {"plugins.api_routers.index"},
    "core/routers/metrics.py": {"plugins.api_routers.metrics"},
    "core/routers/status.py": {"plugins.api_routers.status"},
    "core/routers/tenant.py": {"plugins.api_routers.tenant"},
}

CORE_TO_PLUGIN_IMPORT_PREFIX_ALLOWLIST = {
    "core/scraper/": {"plugins.web_scraper"},
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

        if relative_path.startswith("core/"):
            if (
                relative_path.startswith(frozen_prefixes)
                and relative_path not in legacy_allowlist
            ):
                violations.append(
                    f"{relative_path}: new legacy/domain-specific module added under frozen core path"
                )

            for module_name in imported_modules(path):
                if not module_name.startswith("plugins"):
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

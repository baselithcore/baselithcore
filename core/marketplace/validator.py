"""
Marketplace Plugin Validator.

Ensures that plugins downloaded from the marketplace adhere to the
framework's expected structure and security requirements.
"""

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional
from .models import MarketplacePlugin

import yaml

logger = logging.getLogger(__name__)


@dataclass
class ValidationIssue:
    """A specific issue found during plugin validation."""

    level: str  # "error" or "warning"
    message: str
    file: Optional[str] = None


@dataclass
class ValidationResult:
    """Overall result of a plugin validation process."""

    is_valid: bool
    issues: List[ValidationIssue] = field(default_factory=list)
    metadata: Optional[MarketplacePlugin] = None

    @property
    def errors(self) -> List[ValidationIssue]:
        return [i for i in self.issues if i.level == "error"]

    @property
    def warnings(self) -> List[ValidationIssue]:
        return [i for i in self.issues if i.level == "warning"]


class PluginValidator:
    """
    Validates the structure and content of a plugin directory.
    """

    def validate(self, plugin_path: Path) -> ValidationResult:
        """
        Perform a full validation of a plugin directory.
        """
        issues = []
        metadata = None

        if not plugin_path.exists():
            return ValidationResult(
                is_valid=False,
                issues=[
                    ValidationIssue(level="error", message="Plugin path does not exist")
                ],
            )

        # Check for mandatory files
        # 1. __init__.py
        if not (plugin_path / "__init__.py").exists():
            issues.append(
                ValidationIssue(
                    level="error",
                    message="Missing __init__.py. Plugin must be a Python package.",
                )
            )

        # 2. manifest.yaml (preferred) or pyproject.toml
        manifest_path = plugin_path / "manifest.yaml"
        pyproject_path = plugin_path / "pyproject.toml"

        if not manifest_path.exists() and not pyproject_path.exists():
            issues.append(
                ValidationIssue(
                    level="error",
                    message="Missing manifest.yaml or pyproject.toml. Plugin metadata is required.",
                )
            )

        # Validate manifest if it exists
        if manifest_path.exists():
            try:
                with open(manifest_path, "r") as f:
                    manifest = yaml.safe_load(f)
                    if not manifest or "name" not in manifest:
                        issues.append(
                            ValidationIssue(
                                level="error",
                                message="Invalid manifest.yaml: 'name' field is missing.",
                                file="manifest.yaml",
                            )
                        )
                    else:
                        try:
                            # Extract metadata into MarketplacePlugin model
                            metadata = MarketplacePlugin(
                                id=manifest.get("id", plugin_path.name),
                                name=manifest.get("name"),
                                version=manifest.get("version", "0.1.0"),
                                description=manifest.get("description"),
                                author=manifest.get("author", "unknown"),
                                category=manifest.get("category", "other"),
                                repository=manifest.get("repository")
                                or manifest.get("git_url"),
                                homepage=manifest.get("homepage"),
                                license=manifest.get("license", "MIT"),
                                python_requires=manifest.get(
                                    "python_requires", ">=3.10"
                                ),
                            )
                        except Exception as e:
                            issues.append(
                                ValidationIssue(
                                    level="warning",
                                    message=f"Manifest exists but is incomplete for marketplace indexing: {e}",
                                    file="manifest.yaml",
                                )
                            )
            except Exception as e:
                issues.append(
                    ValidationIssue(
                        level="error",
                        message=f"Failed to parse manifest.yaml: {e}",
                        file="manifest.yaml",
                    )
                )

        # Check for optional but recommended files
        if not (plugin_path / "router.py").exists():
            issues.append(
                ValidationIssue(
                    level="warning",
                    message="Missing router.py. Plugin will not have an API exposure.",
                )
            )

        is_valid = not any(i.level == "error" for i in issues)
        return ValidationResult(is_valid=is_valid, issues=issues, metadata=metadata)

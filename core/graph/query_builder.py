"""
Query construction and parameter encoding utilities for FalkorDB/RedisGraph.

Provides type-safe Cypher query building and parameter encoding.
"""

from __future__ import annotations

import json
import re
from core.observability import get_logger
from typing import Any, Mapping, Sequence

logger = get_logger(__name__)


def build_query(cypher: str, params: Mapping[str, Any]) -> str:
    """
    Build a parameterized Cypher query with CYPHER prefix.

    Args:
        cypher: Base Cypher query string
        params: Parameter dictionary to inject

    Returns:
        Full query string with parameters encoded
    """
    if not params:
        return cypher
    assignments = []
    for key, value in params.items():
        if not key:
            continue
        assignments.append(f"{key}={encode_param(value)}")
    prefix = " ".join(assignments)
    return f"CYPHER {prefix} {cypher}" if prefix else cypher


def encode_param(value: Any) -> str:
    """
    Encode a Python value to Cypher parameter syntax.

    Handles: None, bool, int, float, str, list, tuple, set, dict

    Args:
        value: Python value to encode

    Returns:
        Cypher-compatible string representation
    """
    if value is None:
        return "NULL"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return repr(value)
    if isinstance(value, str):
        # Clean unicode escape sequences if they appear literally in the string
        # e.g. "Analysis \u2011 Part 1" -> "Analysis ‑ Part 1"
        val_to_dump = value
        if "\\u" in val_to_dump:
            try:
                val_to_dump = re.sub(
                    r"\\u([0-9a-fA-F]{4})",
                    lambda m: chr(int(m.group(1), 16)),
                    val_to_dump,
                )
            except Exception:
                logger.debug(
                    "Unicode escape sequence cleanup failed, using original string"
                )
        return json.dumps(val_to_dump)
    if isinstance(value, (list, tuple, set)):
        return "[" + ", ".join(encode_param(v) for v in value) + "]"
    if isinstance(value, Mapping):
        parts = []
        for key, val in value.items():
            clean_key = sanitize_label(str(key))
            if not clean_key:
                continue
            parts.append(f"{clean_key}: {encode_param(val)}")
        return "{" + ", ".join(parts) + "}"
    return json.dumps(str(value))


def format_labels(labels: Sequence[str]) -> str:
    """
    Format a sequence of labels for Cypher node/relationship syntax.

    Args:
        labels: List of label strings

    Returns:
        Formatted label string like ":Label1:Label2" or empty string
    """
    clean = [sanitize_label(label) for label in labels if label]
    clean = [label for label in clean if label]
    return ":" + ":".join(clean) if clean else ""


def sanitize_label(label: str) -> str:
    """
    Sanitize a label to contain only alphanumeric characters and underscores.

    Args:
        label: Raw label string

    Returns:
        Sanitized label safe for Cypher queries
    """
    return "".join(ch for ch in label.strip() if ch.isalnum() or ch == "_")

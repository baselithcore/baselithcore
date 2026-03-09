"""
Database Serializers.

Handles serialization and deserialization of complex types (like source lists) for database storage.
"""

from __future__ import annotations

import json
from typing import Any, Dict, Iterable, List, Optional


def serialize_sources(
    sources: Optional[Iterable[Dict[str, Any]]],
) -> Optional[str]:
    """Serializza la lista di fonti in JSON, omettendo valori falsy."""

    if not sources:
        return None

    cleaned: List[Dict[str, Any]] = []
    for source in sources:
        if not isinstance(source, dict):
            continue
        entry: Dict[str, Any] = {}
        for key in ("document_id", "title", "path", "url", "origin", "source_type"):
            value = source.get(key)
            if isinstance(value, str):
                value = value.strip()
            if value:
                entry[key] = value
        score_value = source.get("score")
        if score_value is not None:
            try:
                entry["score"] = float(score_value)
            except (TypeError, ValueError):
                pass
        if entry:
            cleaned.append(entry)

    if not cleaned:
        return None
    try:
        return json.dumps(cleaned)
    except (TypeError, ValueError):
        return None


def deserialize_sources(raw_sources: Optional[str]) -> List[Dict[str, Any]]:
    """Converte il JSON delle fonti in strutture Python leggibili."""

    if not raw_sources:
        return []
    try:
        decoded = json.loads(raw_sources)
    except (TypeError, ValueError, json.JSONDecodeError):
        return []

    results: List[Dict[str, Any]] = []
    if not isinstance(decoded, list):
        return results

    for item in decoded:
        if not isinstance(item, dict):
            continue
        entry: Dict[str, Any] = {}
        for key in ("document_id", "title", "path", "url", "origin", "source_type"):
            value = item.get(key)
            if isinstance(value, str):
                value = value.strip()
            if value:
                entry[key] = value
        score_value = item.get("score")
        if score_value is not None:
            try:
                entry["score"] = float(score_value)
            except (TypeError, ValueError):
                pass
        if entry:
            results.append(entry)
    return results

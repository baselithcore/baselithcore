"""
Feedback Boost Utilities.

Provides feedback-based score adjustment for ranked hits.
Migrated from app/chat/feedback.py
"""

from __future__ import annotations

from typing import Any, Dict, List, Sequence, Tuple

RankedHit = Tuple[Any, float]


def apply_feedback_boost(
    ranked_hits: Sequence[RankedHit],
    feedback_stats: Dict[str, Dict[str, Any]],
    *,
    min_total: int,
    positive_weight: float,
    negative_weight: float,
) -> List[RankedHit]:
    """
    Apply feedback-based score adjustments to ranked hits.

    Args:
        ranked_hits: List of (hit, score) tuples
        feedback_stats: Dict mapping document IDs to feedback statistics
        min_total: Minimum feedback count to apply boost
        positive_weight: Weight for positive feedback
        negative_weight: Weight for negative feedback

    Returns:
        Adjusted and re-sorted ranked hits
    """
    if not ranked_hits or not feedback_stats:
        return list(ranked_hits)

    adjusted_hits: List[RankedHit] = []
    for hit, score in ranked_hits:
        payload = getattr(hit, "payload", None) or {}
        doc_raw = payload.get("document_id")
        doc_key = str(doc_raw) if doc_raw is not None else str(getattr(hit, "id", ""))
        candidate_keys = [f"id::{doc_key}"]

        relative_path = payload.get("relative_path")
        if isinstance(relative_path, str) and relative_path.strip():
            candidate_keys.append(f"path::{relative_path.strip()}")

        source_value = payload.get("source")
        if isinstance(source_value, str) and source_value.strip():
            alias = source_value.strip()
            if alias.startswith("http"):
                candidate_keys.append(f"url::{alias}")
            else:
                candidate_keys.append(f"path::{alias}")

        entry = None
        for candidate in candidate_keys:
            entry = feedback_stats.get(candidate)
            if entry:
                break

        if entry and entry.get("total", 0) >= min_total:
            adjustment = (
                entry.get("positives", 0) * positive_weight
                - entry.get("negatives", 0) * negative_weight
            )
            score += adjustment

        adjusted_hits.append((hit, score))

    return sorted(adjusted_hits, key=lambda item: item[1], reverse=True)


__all__ = ["apply_feedback_boost", "RankedHit"]

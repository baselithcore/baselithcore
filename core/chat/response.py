"""
Response Utilities.

Helper functions for processing and formatting LLM responses.
Migrated from app/chat/response.py
"""

from __future__ import annotations

import re
from typing import Any, Dict, Iterable, List, Sequence


def append_sources(answer: str, doc_sources: Sequence[Dict[str, Any]]) -> str:
    """
    Append source links to the answer.

    Args:
        answer: The LLM-generated answer
        doc_sources: List of source documents with metadata

    Returns:
        Answer with sources appended
    """
    sources_lines: List[str] = []
    for entry in _render_sources(doc_sources):
        sources_lines.append(entry)

    if not sources_lines:
        return answer

    sources_text = "\n".join(sources_lines)
    trimmed_answer = answer.rstrip()
    if trimmed_answer:
        return f"{trimmed_answer}\n\nFonti:\n{sources_text}"
    return f"Fonti:\n{sources_text}"


def ensure_string_answer(answer: Any) -> str:
    """Ensure the answer is a string."""
    if isinstance(answer, str):
        return answer
    return str(answer)


def strip_sources_section(answer: str) -> str:
    """
    Remove trailing sections that list sources (e.g. "Fonti:") from the model output.
    The UI already shows sources separately, so we drop any source block the model adds.
    """
    header_pattern = re.compile(r"(?im)(?:^|\\n)(fonti|sources)\\s*:\\s*\\n")
    match = header_pattern.search(answer)
    if not match:
        return answer

    cut_pos = match.start()
    cleaned = answer[:cut_pos].rstrip()
    return cleaned


def _render_sources(doc_sources: Iterable[Dict[str, Any]]) -> Iterable[str]:
    """Render source documents as formatted strings."""
    seen_urls = set()
    seen_paths = set()

    for source in doc_sources:
        if not isinstance(source, dict):
            continue

        metrics = _collect_metrics(source)
        metrics_text = f" ({', '.join(metrics)})" if metrics else ""

        url = source.get("url")
        url_clean = url.strip() if isinstance(url, str) else ""
        if url_clean and url_clean.startswith("http"):
            if url_clean in seen_urls:
                continue
            seen_urls.add(url_clean)
            title = source.get("title")
            title_clean = title.strip() if isinstance(title, str) else ""
            if not title_clean:
                title_clean = url_clean
            yield f"- [Fonte]({url_clean}) - {title_clean}{metrics_text}"
            continue

        path = source.get("path")
        path_clean = path.strip() if isinstance(path, str) else ""
        if path_clean and path_clean not in seen_paths:
            seen_paths.add(path_clean)
            title = source.get("title")
            if isinstance(title, str) and title.strip():
                title_clean = title.strip()
            else:
                title_clean = path_clean
            escaped_path = (
                path_clean.replace("&", "&amp;")
                .replace("<", "&lt;")
                .replace(">", "&gt;")
            )
            yield f"- File: <code>{escaped_path}</code> - {title_clean}{metrics_text}"


def _collect_metrics(source: Dict[str, Any]) -> List[str]:
    """Collect relevance metrics from a source."""
    metrics_parts: List[str] = []
    coverage = source.get("context_ratio")
    if isinstance(coverage, (int, float)) and coverage > 0:
        metrics_parts.append(f"copertura {coverage * 100:.0f}%")
    score_value = source.get("score_avg")
    if not isinstance(score_value, (int, float)):
        score_value = source.get("score")
    if isinstance(score_value, (int, float)):
        metrics_parts.append(f"score {score_value:.2f}")
    return metrics_parts


__all__ = ["append_sources", "ensure_string_answer", "strip_sources_section"]

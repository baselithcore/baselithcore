"""
Context Building Utilities.

Provides functions for building context from ranked document hits.
Migrated from app/chat/context.py
"""

from typing import Any, Dict, List, Sequence, Tuple, Optional


def _to_int(value: Any) -> Any:
    if isinstance(value, int):
        return value
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _chunk_order(payload: Dict[str, Any]) -> int:
    index = _to_int(payload.get("chunk_index"))
    return index if index is not None else 0


def _build_metadata(first_payload: Dict[str, Any], newline: str) -> List[str]:
    metadata_lines: List[str] = []

    title = first_payload.get("title")
    if isinstance(title, str) and title.strip():
        metadata_lines.append(f"Titolo: {title.strip()}")

    url = first_payload.get("url")
    if isinstance(url, str) and url.strip():
        metadata_lines.append(f"Fonte: {url.strip()}")
    else:
        source = first_payload.get("source")
        if isinstance(source, str) and source.strip().startswith("http"):
            metadata_lines.append(f"Fonte: {source.strip()}")

    origin = first_payload.get("origin")
    origin_normalized = origin.strip().lower() if isinstance(origin, str) else ""
    if origin_normalized == "filesystem":
        relative_path = first_payload.get("relative_path")
        if isinstance(relative_path, str) and relative_path.strip():
            metadata_lines.append(f"Percorso: {relative_path.strip()}")
        category = first_payload.get("category")
        if isinstance(category, str) and category.strip():
            metadata_lines.append(f"Cartella: {category.strip()}")
        doc_type = first_payload.get("doc_type")
        if isinstance(doc_type, str) and doc_type.strip():
            metadata_lines.append(f"Tipo: {doc_type.strip()}")

    return metadata_lines


def _build_context_block(
    payloads: Sequence[Dict[str, Any]],
    *,
    newline: str,
    double_newline: str,
) -> str:
    if not payloads:
        return ""

    sorted_payloads = sorted(payloads, key=_chunk_order)
    first_payload = sorted_payloads[0]
    metadata_lines = _build_metadata(first_payload, newline)

    chunk_bodies: List[str] = []
    for payload in sorted_payloads:
        chunk_body = (payload.get("chunk_body") or payload.get("text") or "").strip()
        if chunk_body:
            chunk_bodies.append(chunk_body)

    block_parts: List[str] = []
    if metadata_lines:
        block_parts.append(newline.join(metadata_lines))
    if chunk_bodies:
        block_parts.append(double_newline.join(chunk_bodies))

    return double_newline.join(part for part in block_parts if part).strip()


def _extract_doc_source(
    doc_key: str,
    payloads: Sequence[Dict[str, Any]],
    score: Optional[float],
) -> Optional[Dict[str, Any]]:
    if not payloads:
        return None

    sorted_payloads = sorted(payloads, key=_chunk_order)
    first_payload = sorted_payloads[0]
    origin = first_payload.get("origin")
    origin_normalized = origin.strip().lower() if isinstance(origin, str) else ""

    base_info: Dict[str, Any] = {"document_id": doc_key}
    if origin_normalized:
        base_info["origin"] = origin_normalized
    if score is not None:
        base_info["score"] = score

    url = first_payload.get("url")
    if isinstance(url, str):
        url_clean = url.strip()
        if url_clean:
            title = first_payload.get("title")
            if isinstance(title, str) and title.strip():
                title_clean = title.strip()
            else:
                title_clean = url_clean
            info = dict(base_info)
            info.update({"title": title_clean, "url": url_clean, "source_type": "url"})
            return info

    source = first_payload.get("source")
    if isinstance(source, str):
        source_clean = source.strip()
        if source_clean.startswith("http"):
            title = first_payload.get("title")
            if isinstance(title, str) and title.strip():
                title_clean = title.strip()
            else:
                title_clean = source_clean
            info = dict(base_info)
            info.update(
                {"title": title_clean, "url": source_clean, "source_type": "url"}
            )
            return info

    if origin_normalized == "filesystem":
        relative_path = first_payload.get("relative_path")
        if isinstance(relative_path, str) and relative_path.strip():
            location = relative_path.strip()
        elif isinstance(source, str) and source.strip():
            location = source.strip()
        else:
            location = ""

        if location:
            title = first_payload.get("title")
            if isinstance(title, str) and title.strip():
                title_clean = title.strip()
            else:
                filename = first_payload.get("filename")
                if isinstance(filename, str) and filename.strip():
                    title_clean = filename.strip()
                else:
                    title_clean = location
            info = dict(base_info)
            info.update({"title": title_clean, "path": location, "source_type": "path"})
            return info

    return None


def _build_blocks_and_sources(
    order: Sequence[str],
    chunks: Dict[str, Dict[str, Any]],
    scores: Dict[str, float],
    *,
    score_lists: Dict[str, List[float]],
    newline: str,
    double_newline: str,
) -> Tuple[List[str], List[Dict[str, Any]], Dict[str, Dict[str, Any]]]:
    blocks: List[str] = []
    sources: List[Dict[str, Any]] = []
    doc_stats: Dict[str, Dict[str, Any]] = {}
    for doc_key in order:
        payloads = chunks.get(doc_key, {}).get("payloads", [])
        block = _build_context_block(
            payloads, newline=newline, double_newline=double_newline
        )
        if block:
            blocks.append(block)
        doc_info = _extract_doc_source(doc_key, payloads, scores.get(doc_key))
        if doc_info:
            sources.append(doc_info)

        chunk_bodies: List[str] = []
        total_chars = 0
        for payload in payloads:
            chunk_body = (
                payload.get("chunk_body") or payload.get("text") or ""
            ).strip()
            if chunk_body:
                chunk_bodies.append(chunk_body)
                total_chars += len(chunk_body)

        raw_scores = score_lists.get(doc_key, [])
        avg_score = sum(raw_scores) / len(raw_scores) if raw_scores else None

        doc_stats[doc_key] = {
            "chunk_count": len(chunk_bodies),
            "chunk_chars": total_chars,
            "avg_score": avg_score,
            "max_score": scores.get(doc_key),
        }

    return blocks, sources, doc_stats


def build_context_and_sources(
    ranked_hits: Sequence[Tuple[Any, float]],
    *,
    final_top_k: int,
    newline: str,
    double_newline: str,
    section_separator: str,
    max_chunks_per_document: int = 3,
    min_rerank_score: float = 0.15,
) -> Tuple[str, List[Dict[str, Any]]]:
    """
    Construct a consolidated context string and a list of sources from ranked search hits.

    This function processes the results of a vector search (or re-ranking),
    groups chunks by document, enforces max chunk limits per document,
    filters by score, and formats the output for LLM consumption.

    Args:
        ranked_hits: A sequence of (hit, score) tuples.
        final_top_k: Maximum number of unique documents to include in the context.
        newline: Character(s) to use for single newlines.
        double_newline: Character(s) to use for paragraph breaks.
        section_separator: String used to separate blocks from different documents.
        max_chunks_per_document: Maximum number of chunks to include for any single document.
        min_rerank_score: Minimum relevance score required to include a document.

    Returns:
        A tuple containing:
            - The formatted context string.
            - A list of dictionaries containing metadata for the used sources.
    """
    MAX_DOCUMENTS = final_top_k
    doc_order: List[str] = []
    doc_chunks: Dict[str, Dict[str, Any]] = {}
    doc_scores: Dict[str, float] = {}
    doc_score_lists: Dict[str, List[float]] = {}

    for hit, score in ranked_hits:
        payload = getattr(hit, "payload", None) or {}
        doc_raw = payload.get("document_id")
        doc_key = str(doc_raw) if doc_raw is not None else str(getattr(hit, "id", ""))
        entry = doc_chunks.get(doc_key)

        if entry is None:
            if score < min_rerank_score and doc_order:
                continue
            if len(doc_order) >= MAX_DOCUMENTS:
                continue
            entry = {
                "payloads": [],
                "primary_index": _to_int(payload.get("chunk_index")),
            }
            doc_chunks[doc_key] = entry
            doc_order.append(doc_key)
            try:
                doc_scores[doc_key] = float(score)
            except (TypeError, ValueError):
                pass

        chunk_index_int = _to_int(payload.get("chunk_index"))
        if chunk_index_int is not None:
            existing_indices = {
                idx
                for idx in (
                    _to_int(item.get("chunk_index")) for item in entry["payloads"]
                )
                if idx is not None
            }
            if chunk_index_int in existing_indices:
                continue
            primary_index = entry.get("primary_index")
            if primary_index is not None and abs(chunk_index_int - primary_index) > 2:
                continue

        if len(entry["payloads"]) >= max_chunks_per_document:
            continue

        entry["payloads"].append(payload)
        if entry.get("primary_index") is None and chunk_index_int is not None:
            entry["primary_index"] = chunk_index_int

        try:
            score_float = float(score)
        except (TypeError, ValueError):
            score_float = None
        if score_float is not None:
            previous_score = doc_scores.get(doc_key)
            if previous_score is None or score_float > previous_score:
                doc_scores[doc_key] = score_float
            doc_score_lists.setdefault(doc_key, []).append(score_float)

    top_chunks, doc_sources, doc_stats = _build_blocks_and_sources(
        doc_order,
        doc_chunks,
        doc_scores,
        score_lists=doc_score_lists,
        newline=newline,
        double_newline=double_newline,
    )

    if not top_chunks and ranked_hits:
        doc_order = []
        doc_chunks = {}
        for hit, fallback_score in ranked_hits[:final_top_k]:
            payload = getattr(hit, "payload", None) or {}
            doc_raw = payload.get("document_id")
            doc_key = (
                str(doc_raw) if doc_raw is not None else str(getattr(hit, "id", ""))
            )
            entry = doc_chunks.setdefault(
                doc_key,
                {
                    "payloads": [],
                    "primary_index": _to_int(payload.get("chunk_index")),
                },
            )
            if doc_key not in doc_order and len(doc_order) < final_top_k:
                doc_order.append(doc_key)
            appended = False
            if len(entry["payloads"]) < max_chunks_per_document:
                entry["payloads"].append(payload)
                appended = True
            try:
                fallback_score_float = float(fallback_score)
            except (TypeError, ValueError):
                fallback_score_float = None
            if fallback_score_float is not None:
                previous_score = doc_scores.get(doc_key)
                if previous_score is None or fallback_score_float > previous_score:
                    doc_scores[doc_key] = fallback_score_float
                if appended:
                    doc_score_lists.setdefault(doc_key, []).append(fallback_score_float)

        top_chunks, doc_sources, doc_stats = _build_blocks_and_sources(
            doc_order,
            doc_chunks,
            doc_scores,
            score_lists=doc_score_lists,
            newline=newline,
            double_newline=double_newline,
        )

    context = section_separator.join(top_chunks)
    total_chars = sum(stats.get("chunk_chars", 0) for stats in doc_stats.values())

    for doc_info in doc_sources:
        info_doc_key = doc_info.get("document_id")
        if not isinstance(info_doc_key, str):
            continue

        stats = doc_stats.get(info_doc_key)
        if not stats:
            continue

        chunk_count = stats.get("chunk_count", 0)
        chunk_chars = stats.get("chunk_chars", 0)
        avg_score = stats.get("avg_score")

        doc_info["chunks_used"] = chunk_count
        doc_info["chunk_coverage_ratio"] = (
            chunk_count / max_chunks_per_document if max_chunks_per_document else 0.0
        )
        if avg_score is not None:
            doc_info["score_avg"] = avg_score
        doc_info["context_chars"] = chunk_chars
        doc_info["context_ratio"] = (chunk_chars / total_chars) if total_chars else 0.0

    return context, doc_sources

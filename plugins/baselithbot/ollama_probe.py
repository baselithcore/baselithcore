"""Runtime discovery of locally-installed Ollama models.

Queries the Ollama HTTP API (``GET /api/tags``) and classifies the returned
tags into *vision-capable* vs *text-only* models so the dashboard can offer
an accurate picker instead of a hardcoded short list.
"""

from __future__ import annotations

from core.observability.logging import get_logger

logger = get_logger(__name__)

_VISION_NAME_HINTS = (
    "llava",
    "bakllava",
    "moondream",
    "minicpm-v",
    "minicpm-o",
    "vision",
    "qwen2-vl",
    "qwen2.5-vl",
    "qwen2vl",
    "qwen25vl",
    "qwenvl",
    "-vl",
    "llama3.2-vision",
    "llama-3.2-vision",
    "pixtral",
    "internvl",
    "cogvlm",
    "granite-vision",
    "mllama",
    "gemma3",
    "paligemma",
)
_EMBED_NAME_HINTS = ("embed", "embedding")
_EMBED_FAMILIES = {"bert", "nomic-bert", "mxbai-embed", "snowflake-arctic-embed"}
_VISION_FAMILIES = {
    "clip",
    "qwen25vl",
    "qwen2vl",
    "mllama",
    "llava",
    "moondream",
    "internvl",
    "paligemma",
    "granitevision",
    "pixtral",
    "minicpmv",
}


def _classify(name: str, families: list[str]) -> str | None:
    """Return ``"vision"``, ``"llm"``, or ``None`` (embedding — skip)."""
    fam_set = {f.lower() for f in families or []}
    lname = name.lower()
    if fam_set & _EMBED_FAMILIES or any(h in lname for h in _EMBED_NAME_HINTS):
        return None
    if fam_set & _VISION_FAMILIES or any(h in lname for h in _VISION_NAME_HINTS):
        return "vision"
    if any("vl" == f or f.endswith("vl") for f in fam_set):
        return "vision"
    return "llm"


async def fetch_ollama_catalog(
    base_url: str, timeout: float = 3.0
) -> dict[str, list[str]]:
    """Return ``{"llm": [...], "vision": [...]}`` of installed Ollama tags.

    Never raises: on probe failure returns empty lists so callers can fall
    back to a static catalog.
    """
    try:
        import httpx
    except ImportError:
        logger.warning("ollama_probe_httpx_missing")
        return {"llm": [], "vision": []}

    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.get(f"{base_url.rstrip('/')}/api/tags")
            resp.raise_for_status()
            data = resp.json()
    except Exception as exc:
        logger.warning("ollama_probe_failed", url=base_url, error=str(exc))
        return {"llm": [], "vision": []}

    llm: list[str] = []
    vision: list[str] = []
    for entry in data.get("models", []) or []:
        name = entry.get("name") or ""
        if not name:
            continue
        families = ((entry.get("details") or {}).get("families")) or []
        bucket = _classify(name, families)
        if bucket == "vision":
            vision.append(name)
        elif bucket == "llm":
            llm.append(name)

    return {"llm": sorted(llm), "vision": sorted(vision)}


__all__ = ["fetch_ollama_catalog"]

"""Set-of-Mark (SoM) DOM overlay for Playwright pages.

Inspired by the Microsoft Set-of-Mark paper (arxiv 2310.11441). Instead of
feeding a VLM raw screenshots, overlay numbered bounding boxes on every
clickable element and let the model say "click 7" — higher accuracy, fewer
coordinate errors, more cache-friendly prompts.

Usage
-----
    marks = await annotate(page)             # inject + collect metadata
    # ... send screenshot to the VLM ...
    await clear(page)                        # remove overlay before action

Every call returns a list of :class:`SomMark` entries with ``index``,
``selector`` (best-effort unique), ``bbox``, ``role``, and a truncated text
preview. Callers should pass these back into the VLM prompt so it can
resolve mark indices to concrete actions.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from core.observability.logging import get_logger

logger = get_logger(__name__)


_INJECT_JS = r"""
((opts) => {
  const MAX = opts && opts.max ? Number(opts.max) : 60;
  const prev = document.getElementById('__baselithbot_som__');
  if (prev) prev.remove();

  const CLICKABLE = [
    'a[href]', 'button', 'input:not([type=hidden])', 'select', 'textarea',
    '[role=button]', '[role=link]', '[role=tab]', '[role=menuitem]',
    '[onclick]', '[tabindex]:not([tabindex="-1"])',
  ].join(',');

  const container = document.createElement('div');
  container.id = '__baselithbot_som__';
  Object.assign(container.style, {
    position: 'fixed', top: '0', left: '0', width: '100%', height: '100%',
    pointerEvents: 'none', zIndex: '2147483647',
  });

  const marks = [];
  let index = 0;
  for (const el of document.querySelectorAll(CLICKABLE)) {
    if (index >= MAX) break;
    const rect = el.getBoundingClientRect();
    if (rect.width < 6 || rect.height < 6) continue;
    if (rect.bottom < 0 || rect.top > window.innerHeight) continue;
    if (rect.right < 0 || rect.left > window.innerWidth) continue;
    const style = window.getComputedStyle(el);
    if (style.visibility === 'hidden' || style.display === 'none') continue;

    const box = document.createElement('div');
    Object.assign(box.style, {
      position: 'fixed',
      left: rect.left + 'px',
      top: rect.top + 'px',
      width: rect.width + 'px',
      height: rect.height + 'px',
      boxSizing: 'border-box',
      border: '2px solid #ff3366',
      backgroundColor: 'rgba(255,51,102,0.05)',
    });

    const label = document.createElement('span');
    label.textContent = String(index);
    Object.assign(label.style, {
      position: 'absolute', top: '-10px', left: '-4px',
      padding: '1px 4px', fontSize: '11px', lineHeight: '1.1',
      fontFamily: 'ui-monospace, Menlo, monospace',
      color: '#fff', backgroundColor: '#ff3366', borderRadius: '3px',
      boxShadow: '0 1px 2px rgba(0,0,0,0.5)',
    });
    box.appendChild(label);
    container.appendChild(box);

    const text = (el.innerText || el.value || el.getAttribute('aria-label') || '').trim();
    marks.push({
      index: index,
      tag: el.tagName.toLowerCase(),
      role: el.getAttribute('role') || null,
      text: text.slice(0, 120),
      href: el.getAttribute('href') || null,
      bbox: {
        x: Math.round(rect.left),
        y: Math.round(rect.top),
        w: Math.round(rect.width),
        h: Math.round(rect.height),
      },
    });
    el.setAttribute('data-baselithbot-som', String(index));
    index += 1;
  }

  document.documentElement.appendChild(container);
  return marks;
})(arguments[0])
"""

_CLEAR_JS = r"""
(() => {
  const node = document.getElementById('__baselithbot_som__');
  if (node) node.remove();
  for (const el of document.querySelectorAll('[data-baselithbot-som]')) {
    el.removeAttribute('data-baselithbot-som');
  }
  return true;
})()
"""


@dataclass
class SomMark:
    """Metadata for one numbered mark overlaid on the page."""

    index: int
    tag: str
    role: str | None
    text: str
    href: str | None
    bbox: dict[str, int]

    def to_dict(self) -> dict[str, Any]:
        return {
            "index": self.index,
            "tag": self.tag,
            "role": self.role,
            "text": self.text,
            "href": self.href,
            "bbox": dict(self.bbox),
        }


async def annotate(page: Any, *, max_marks: int = 60) -> list[SomMark]:
    """Inject SoM overlay into ``page`` and return the mark metadata.

    ``page`` must be a Playwright ``Page`` or any duck-typed object exposing
    an awaitable ``evaluate`` method. Callers should await :func:`clear`
    before taking actions so the labels do not interfere with clicks.
    """
    try:
        raw = await page.evaluate(_INJECT_JS, {"max": max_marks})
    except Exception as exc:
        logger.warning("baselithbot_som_annotate_failed", error=str(exc))
        return []
    marks: list[SomMark] = []
    for entry in raw or []:
        marks.append(
            SomMark(
                index=int(entry.get("index", 0)),
                tag=str(entry.get("tag", "")),
                role=entry.get("role"),
                text=str(entry.get("text", "")),
                href=entry.get("href"),
                bbox=dict(entry.get("bbox", {})),
            )
        )
    logger.info("baselithbot_som_annotated", count=len(marks))
    return marks


async def clear(page: Any) -> bool:
    """Remove every mark + data-attribute injected by :func:`annotate`."""
    try:
        return bool(await page.evaluate(_CLEAR_JS))
    except Exception as exc:
        logger.warning("baselithbot_som_clear_failed", error=str(exc))
        return False


def build_som_tool_definition(plugin: Any) -> dict[str, Any]:
    """Build the MCP tool wrapper so an agent can call SoM explicitly."""

    async def baselithbot_som_annotate(
        max_marks: int = 60, clear_after: bool = False
    ) -> dict[str, Any]:
        agent = plugin.agent
        if agent is None or agent._backend is None:
            return {"status": "error", "error": "backend not started"}
        page = getattr(agent._backend, "_page", None)
        if page is None:
            return {"status": "error", "error": "no active page"}
        marks = await annotate(page, max_marks=int(max_marks))
        if clear_after:
            await clear(page)
        return {
            "status": "success",
            "marks": [m.to_dict() for m in marks],
            "count": len(marks),
        }

    return {
        "name": "baselithbot_som_annotate",
        "description": (
            "Overlay numbered Set-of-Mark labels on every clickable element "
            "in the active browser page. Returns the mark metadata so a VLM "
            "can reference elements by index instead of raw coordinates."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "max_marks": {
                    "type": "integer",
                    "default": 60,
                    "description": "Cap on the number of elements to mark.",
                },
                "clear_after": {
                    "type": "boolean",
                    "default": False,
                    "description": (
                        "Remove the overlay immediately after capturing metadata."
                    ),
                },
            },
        },
        "handler": baselithbot_som_annotate,
    }


__all__ = ["SomMark", "annotate", "clear", "build_som_tool_definition"]

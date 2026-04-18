# Set-of-Mark (SoM) vision

[← Index](./README.md)

Source of truth: [`som.py`](../som.py).

## 1. Why

When a vision LLM is asked to click an element from a raw screenshot,
it must produce exact pixel coordinates. This is lossy: small
misalignments miss the target. Set-of-Mark (Microsoft, arXiv 2310.11441)
overlays numbered bounding boxes on clickable elements before capture.
The VLM then reasons at the symbolic level — "click mark 7" — which the
plugin can resolve to a precise click via the mark's recorded bounding
box or CSS selector.

Reported gains: ~20–30 % higher click accuracy on GUI-agent benchmarks,
and fewer degenerate plans when elements are small or densely packed.

## 2. Module shape

```python
from plugins.baselithbot.som import annotate, clear, SomMark

marks: list[SomMark] = await annotate(page, max_marks=60)
# ... render screenshot to the VLM with the marks overlay still visible ...
await clear(page)  # remove overlay before executing the chosen click
```

`page` is any object with an awaitable `evaluate(script, arg)` — in
practice a Playwright `Page` from `browser_agent`.

`SomMark` fields:

| Field | Purpose |
|-------|---------|
| `index` | Numeric label rendered on the overlay |
| `tag` | Lowercase HTML tag |
| `role` | ARIA role, if any |
| `text` | First 120 chars of `innerText` / `value` / `aria-label` |
| `href` | `href` attribute for anchors |
| `bbox` | `{x, y, w, h}` viewport coordinates (rounded ints) |

## 3. MCP tool

```python
baselithbot_som_annotate(max_marks: int = 60, clear_after: bool = False)
  -> {"status": "success", "marks": [...], "count": N}
```

Registered automatically by `BaselithbotPlugin.get_mcp_tools()`. The
tool operates on the currently-running Baselithbot agent's Playwright
page, so the agent must already be started (the tool returns
`{"status": "error", "error": "backend not started"}` otherwise).

## 4. Overlay details

- `position: fixed` container with
  `z-index: 2147483647` and `pointer-events: none`, so it never blocks
  clicks that land through it.
- Red (`#ff3366`) 2 px borders, semi-transparent fill, monospace label
  pill anchored at the top-left corner.
- Candidate selector: `a[href]`, `button`, `input:not([type=hidden])`,
  `select`, `textarea`, `[role=button|link|tab|menuitem]`, `[onclick]`,
  `[tabindex]:not([tabindex="-1"])`.
- Skips anything <6 px wide/tall, off-screen, `display:none`, or
  `visibility:hidden`.
- Mark indices are also written as `data-baselithbot-som="N"` attributes
  on the source elements — handy if the agent wants to use a DOM-level
  selector instead of bbox coordinates.
- `clear()` removes the overlay container and every `data-baselithbot-som`
  attribute so subsequent screenshots are clean.

## 5. Typical agent pattern

```text
1. annotate(page)                → marks[]
2. send {screenshot + marks} to VLM
3. VLM replies: "click mark 7"
4. clear(page)
5. mark = marks[7]
6. page.mouse.click(mark.bbox["x"] + mark.bbox["w"] / 2,
                    mark.bbox["y"] + mark.bbox["h"] / 2)
```

Or resolve by `data-baselithbot-som`:

```python
page.locator('[data-baselithbot-som="7"]').click()
```

## 6. Error handling

Both `annotate` and `clear` swallow JS evaluation errors and emit a
structured warning (`baselithbot_som_annotate_failed`,
`baselithbot_som_clear_failed`) — the agent can keep running on the raw
screenshot if SoM injection fails for any reason.

## 7. Testing

Coverage: [`tests/unit/plugins_tests/test_baselithbot_replay_som.py`](../../../tests/unit/plugins_tests/test_baselithbot_replay_som.py)
— duck-typed Page stubs exercise the `annotate`/`clear`/error paths +
verify the MCP tool is registered on the plugin.

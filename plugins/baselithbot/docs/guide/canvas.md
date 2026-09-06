# Canvas

The Live Canvas — an append-only, agent-writable widget surface serialized
as **Anthropic A2UI** JSON, so any A2UI-aware client can render the same
output the dashboard does.

## What the page does

- **Render** a widget payload (`Text`, `Button`, `Image`, `List` widgets) —
  a sample payload is offered as a starting point.
- **Live widget view** of the current surface state.
- **Clear** the surface.
- **Dispatch** — send an interaction event back (e.g. a button press) for
  testing agent-side handlers.

## Backend

`GET /dash/canvas` (snapshot, also feeds the roster count on
[Overview](overview.md)), `POST /dash/canvas/render` (🔒),
`POST /dash/canvas/clear` (🔒), `POST /dash/canvas/dispatch` (🔒).

## Notes

`CanvasSurface` is process-wide (not per-session). The `baselithbot_canvas_render`
MCP tool lets an agent append widgets and get back the serialized A2UI
payload directly, so Canvas doubles as a way to test A2UI output before a
downstream client consumes it — see BaselithCore's A2UI blueprint schema for
the whitelisted component tree this renders into.

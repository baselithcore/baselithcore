# CLI & Python SDK

[← Index](./README.md)

Command-line entry points and programmatic usage patterns.

## 1. CLI

Registered via [`cli.py`](../cli.py) into `core.cli.__main__`.

```bash
baselith baselithbot <command> [options]
```

### 1.1 `run`

Execute one autonomous task.

```bash
baselith baselithbot run "open hacker news and list top 3 stories" \
  --start-url https://news.ycombinator.com --max-steps 25

# Windowed browser (debug)
baselith baselithbot run "click the login button" --headed
```

Arguments:

| Flag | Default | Meaning |
|------|---------|---------|
| `goal` (positional) | — | Natural-language goal |
| `--start-url` | `None` | Optional landing URL |
| `--max-steps` | `20` | Upper bound on the loop |
| `--headed` | off | Show browser window (default: headless) |

Emits JSON to stdout; exit code `0` on success, `1` on failure.

### 1.2 `status`

Print plugin manifest / version / readiness.

```bash
baselith baselithbot status
# → baselithbot: 1.0.0 (alpha)
```

### 1.3 `onboard`

Interactive wizard producing a `plugins.yaml` block.

```bash
baselith baselithbot onboard                  # prints block
baselith baselithbot onboard --write          # merges into configs/plugins.yaml
baselith baselithbot onboard --write --config-path path/to/plugins.yaml
```

Prompts for: headless?, Computer Use?, shell?, filesystem root, audit
log path. Output is deterministic so it can be piped through
`diff`/`git`.

## 2. Python SDK

### 2.1 Direct agent

```python
import asyncio
from plugins.baselithbot import BaselithbotAgent, BaselithbotTask

async def main() -> None:
    agent = BaselithbotAgent(config={"headless": True, "max_steps": 25})
    await agent.startup()
    try:
        result = await agent.execute(
            BaselithbotTask(
                goal="search 'baselithcore' on duckduckgo and return top 3",
                start_url="https://duckduckgo.com",
                extract_fields=["title", "url"],
            ),
            context={"run_id": "demo-1", "on_progress": lambda p: print(p)},
        )
        print(result.model_dump_json(indent=2))
    finally:
        await agent.shutdown()

asyncio.run(main())
```

Input shapes accepted by `execute()`:

- `BaselithbotTask` instance.
- Dict matching the `BaselithbotTask` schema.
- Plain string (coerced to `BaselithbotTask(goal=str, max_steps=agent.max_steps)`).

### 2.2 From plugin instance

```python
from plugins.baselithbot import BaselithbotPlugin

plugin = BaselithbotPlugin()
await plugin.initialize({"headless": True})
agent = await plugin.get_or_start_agent()
# use agent.execute(...)
await plugin.shutdown()
```

### 2.3 Context callbacks

```python
async def on_progress(payload: dict) -> None:
    print(payload["steps_taken"], payload["action"], payload["current_url"])

await agent.execute(task, context={"run_id": "r-1", "on_progress": on_progress})
```

`on_progress` can be sync or async. Payload keys: `steps_taken`,
`current_url`, `action`, `reasoning`, `history`, `extracted_data`,
`last_screenshot_b64`.

## 3. Orchestrator intent bridge

`BaselithbotFlowHandler.handle_browse(query, context)` is invoked by the
BaselithCore orchestrator whenever the user utterance matches the
`baselithbot_browse` intent patterns:

- `"baselithbot"`
- `"browse autonomously"`
- `"navigate web"`
- `"automate browser"`
- `"scrape stealth"`
- `"stealth browse"`

Priority 110 (beats default browser intents). Recognized context keys:

| Key | Type | Meaning |
|-----|------|---------|
| `start_url` | str | URL to land on before reasoning |
| `max_steps` | int | Override default step budget |
| `extract_fields` | list[str] | Fields to capture |

Return envelope:

```json
{
  "status": "success" | "failed",
  "response": "Completed in 7 steps at https://…",
  "data": {
    "final_url": "...",
    "steps_taken": 7,
    "extracted_data": {...},
    "history": [...],
    "error": null
  }
}
```

## 4. MCP client usage

Every MCP tool returns a status-envelope dict. Example (pseudo-code):

```python
result = await mcp_client.call("baselithbot_run_task", {
    "goal": "open https://example.com and extract h1",
    "max_steps": 5,
})
assert result["status"] == "success"
print(result["final_url"], result["extracted_data"])
```

## 5. Extending the plugin

Add a new MCP tool:

1. Implement the coroutine under `tools.py` / `extra_tools.py`.
2. Add a build-definition entry (`name`, `description`, `input_schema`,
   `handler`) to the relevant `build_*_tool_definitions` factory.
3. Wrap the body with `_denied` / `_error` helpers so no exception
   crosses into the orchestrator.
4. Add a mocked unit test under
   `tests/unit/plugins_tests/test_baselithbot_*`.

Add a new inbound channel:

1. Subclass `channels.base.Channel` under `channels/<name>.py`.
2. Register in `channels/bootstrap.build_default_registry`.
3. If the payload shape is not generic, add a parser under
   `inbound/parsers.py` and plumb it in `router._parse_inbound`.

Add a new slash command:

1. Add the name to `chat_commands.SUPPORTED_COMMANDS`.
2. Register a handler via `ChatCommandRouter.register(name, handler)` —
   prefer `slash_defaults.install_default_handlers` for built-ins.

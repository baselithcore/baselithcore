# Browser Agent Plugin

Official BaselithCore plugin for browser automation.

## Capabilities

- autonomous browser agent based on Playwright
- MCP tools for navigation, click, typing and task execution
- visual reasoning via `VisionService`

## Compatibility

During the migration away from `core/agents`, legacy imports from `core.agents.browser_*` remain available as temporary shims.

## SSRF Guard

`navigate()` and `BrowserActionType.NAVIGATE` actions reject URLs whose
hostname resolves to loopback (`127.0.0.0/8`, `::1`), private (`10/8`,
`172.16/12`, `192.168/16`), link-local (`169.254/16`), multicast, or reserved
ranges. Schemes other than `http`/`https` are also blocked.

For local development against trusted internal endpoints, opt out via:

```bash
export BASELITH_BROWSER_ALLOW_INTERNAL=true
```

Do not enable this flag in production — combined with prompt-injected URLs it
turns the headless browser into an internal-network scanner.

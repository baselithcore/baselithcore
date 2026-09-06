---
title: Baselithbot
description: Autonomous multi-channel agent plugin — OpenClaw skills, stealth browsing, desktop control, canvas A2UI, voice, cron, MCP.
---

The `baselithbot` plugin is the flagship autonomous agent shipped with
BaselithCore. It composes the `browser_agent` Playwright backend with an
explicit Observe → Plan → Act cognitive loop, adds OpenClaw-style skills,
desktop-control (`pyautogui` + `mss` vision failover), a live Canvas
(A2UI) surface, multi-channel chat adapters, cron scheduling, a 20-tab
React dashboard, and an MCP tool registry.

Version **1.0.2** (stable readiness). Plugin lives at
[`plugins/baselithbot/`](https://github.com/baselithcore/plugin-baselithbot).

## Why a plugin (Sacred Core compliance)

Baselithbot lives entirely under `plugins/` and never touches `core/`.
Domain-specific concerns — stealth countermeasures, OpenClaw tool
layout, channel adapters, the React dashboard — are kept out of the
framework. `core/` stays domain-agnostic; `baselithbot` composes
primitives exposed through `core.plugins`, `core.services.vision`,
`core.observability.logging`, and the plugin registry.

## Status at release (v1.0.2)

- **95** FastAPI routes mounted under `/baselithbot/` (main router + the
  `/dash` dashboard subrouter).
- **20-tab React dashboard** under `plugins/baselithbot/ui/` — served
  from the compiled bundle in `ui/dist/`. All tab anchor GETs return
  200 against a test client.
- **345 tests** collected from `tests/plugins/baselithbot/` and
  `tests/unit/plugins_tests/test_baselithbot_*.py` — 334 unit + 11
  `@pytest.mark.slow` integration (cron-scheduler lifecycle, SessionManager
  LRU eviction, replay-store SQLite persistence).
- **Packaging**: wheel ≈ 644 KB, 204 files. `ui/src/`,
  `ui/node_modules/`, `__pycache__`, and `*.pyc` artifacts are excluded
  via `[tool.setuptools.exclude-package-data]`, as is the runtime
  `.state/` directory. Besides the Python packages, the only data files
  that ship are those matched by `[tool.setuptools.package-data]`:
  `manifest.yaml`, `assets/*` (`logobg-baselithbot500.png`), `ui/dist/**`,
  `docs/**` and the `skills/**` YAML/JSON descriptors. No
  `catalog-info.yaml` is bundled — the Backstage entity is generated live by
  the exporter (see [Backstage Integration](backstage.md)).
- **CI gates**: `ruff check`, `scripts/check_architecture_boundaries.py`,
  `scripts/check_official_plugin_typing.py` — all green.
- **Security**: dashboard writes are fail-closed (503 without
  `BASELITHBOT_DASHBOARD_TOKEN`). `BASELITHBOT_DASHBOARD_ALLOW_INSECURE=1`
  is dev-only and logs a warning on first use.
- **License**: `AGPL-3.0-only` — a deliberate choice, not an obligation. The
  [Plugin Exception](https://github.com/baselithcore/baselithcore/blob/main/LICENSE.exception)
  lets any plugin that uses the framework as a library pick its own terms;
  baselithbot ships under the same copyleft as the framework it extends.

## Capability surface

| Subsystem                 | Module                                                                                  |
| ------------------------- | -------------------------------------------------------------------------------------- |
| Browser loop + stealth    | `browser/agent.py`, `browser/stealth.py`, `browser/js_whitelist.py`                    |
| Desktop agent             | `desktop_agent/agent.py`                                                                |
| Computer-Use              | `computer_use/desktop_lane.py`, `computer_use/os_control.py`, `computer_use/tools.py`  |
| OpenClaw skills           | `skills/` (registry, loader, ClawHub, writer, bundled)                                  |
| Multi-channel adapters    | `channels/` (Slack, Discord, Telegram, …)                                              |
| Sessions + inbound        | `sessions/`, `inbound/`, `policies/dm_policy.py`                                        |
| Canvas (A2UI)             | `canvas/`                                                                               |
| Voice (wake/TTS/realtime) | `voice/` (`wake.py`, `tts.py`, `elevenlabs.py`, `audio_capture.py`, `realtime_loop.py`, `openai_realtime.py`) |
| Cron (native + custom)    | `cron/` (`cron/scheduler.py`, `cron/custom.py`)                                         |
| Node pairing              | `nodes/`, `policies/dm_policy.py`                                                       |
| Replay + audit            | `control/replay.py`, `control/run_tracker.py`                                           |
| Secret store (Fernet)     | `security/secret_store.py`                                                              |
| Approval gate             | `control/approvals.py`                                                                  |
| MCP tools                 | `_mcp.py`, `control/openclaw_tools.py`, `computer_use/tools.py`                         |
| Dashboard (REST + SSE)    | `dashboard/app.py`, `dashboard/routes/**`                                               |
| React UI                  | `ui/` (Vite + TypeScript, 20 pages)                                                     |

## Building and packaging

The React dashboard must be compiled before the Python wheel is built,
because only `ui/dist/` is bundled:

```bash
cd plugins/baselithbot/ui
npm ci
npm run build
cd -

python -m pip wheel --no-deps --no-build-isolation \
    plugins/baselithbot -w /tmp/baselithbot-wheel

# Sanity check: node_modules must not ship
python -m zipfile -l /tmp/baselithbot-wheel/*.whl | grep -c node_modules
# → 0
```

!!! warning "Re-sign after a UI build"
    `ui/dist/**` is part of the plugin integrity surface since 0.27, so
    `npm run build` changes the plugin hash. Run
    `baselith plugin sign plugins/baselithbot` after rebuilding — before the
    wheel, before publishing — otherwise the declared `integrity_sha256` no
    longer matches the tree and the loader refuses it. During local development
    `BASELITH_SKIP_INTEGRITY_CHECK=true` bypasses the check (inert in
    production). See [Packaging › What is hashed](packaging.md#what-is-hashed).

Publishing to the marketplace is covered in detail in
[`plugins/baselithbot/docs/publishing.md`](https://github.com/baselithcore/baselithcore/blob/main/plugins/baselithbot/docs/publishing.md)
(and the one-click Backstage Scaffolder path in
[Backstage Publish](backstage-publish.md)).

## Runtime configuration

Two env vars gate the dashboard API:

| Variable                               | Purpose                                                               |
| -------------------------------------- | --------------------------------------------------------------------- |
| `BASELITHBOT_DASHBOARD_TOKEN`          | Shared bearer token required on every write endpoint.                |
| `BASELITHBOT_DASHBOARD_ALLOW_INSECURE` | `1` to open writes without a token (local dev only — logs warning).  |

Provider secrets are written **at runtime** — nothing is shipped — to
`provider_keys.enc.json` inside the plugin's `.state/` directory
(`plugins/baselithbot/.state/`, created on first use, git-ignored via
`plugins/*/.state/` and excluded from the wheel). The file is
Fernet-encrypted with the master key from `BASELITHBOT_SECRET_KEY`; when
that variable is unset a key is generated once and persisted next to it as
`.state/.secret_key` (mode `0600`). The dashboard never echoes plaintext —
only `***<last4>` previews.

### Post-write verification (computer-use)

Every `.py` file the agent writes through the computer-use `fs_write` tool
is byte-compiled right after the write (stdlib `py_compile`, run in a
thread — cheap, offline, deterministic):

- On a syntax error the tool result gains
  `verification: "compile failed: …"` (the message embeds the offending
  line) and the file is **deliberately kept on disk** — the marker is the
  agent's feedback loop for reading the error and fixing it.
- On success the result carries `verification: "ok"`.

The check is gated by `ComputerUseConfig.post_write_verify`, whose default
comes from the `BASELITH_POST_WRITE_VERIFY` env flag — **ON** unless set to
`0`/`false`/`no`/`off`; an explicit config value wins over the env default.

Each successful write also dispatches a `post`-phase `ToolHookEvent`
(`baselithbot_fs_write`, metadata carrying the verification outcome) on the
core tool-hook bus, with a `*fs_write` observer logging every outcome —
the first production consumer of the post phase. See
[Orchestration › Tool hooks](../core-modules/orchestration.md#tool-hooks-hookspy).

## Realtime duplex voice

The voice surface ships two shapes. The **sequential pipeline** — wake word
(`voice/wake.py`) → STT → LLM → TTS (`voice/tts.py`, `voice/elevenlabs.py`)
— remains the default. The **realtime duplex loop** is the opt-in
alternative: provider audio streams down while user audio streams up over
the core
[`DuplexVoiceSession`](../core-modules/realtime.md#duplex-voice-sessions-duplexvoicesession)
contract, and the loop interrupts assistant playback the instant the user
starts talking.

- **`OpenAIRealtimeSession`** (`voice/openai_realtime.py`) implements the
  core protocol over the OpenAI Realtime WebSocket (`aiohttp`). It connects
  lazily on first use, immediately sending `session.update` with
  `server_vad` turn detection (configurable `silence_duration_ms`), and
  issues `response.cancel` for barge-in. The WebSocket factory is an
  injectable seam (`ws_connect`), so unit tests drive the adapter with
  scripted fakes — no real network. The API key comes from the core voice
  config (`VOICE_OPENAI_API_KEY` / `OPENAI_API_KEY`) as a `SecretStr`.
- **`RealtimeVoiceLoop`** (`voice/realtime_loop.py`) consumes session
  events and drives an `AudioPlayer` (protocol: `play` / `stop` /
  `playing`; `BufferedAudioPlayer` forwards chunks to any injected async
  byte sink). **Barge-in**: a `SpeechStarted` event while assistant audio
  is playing triggers `session.cancel_response()` plus `player.stop()`
  immediately. The loop also measures **response latency** —
  `SpeechStopped` to the first `AudioDelta` — and logs a warning when it
  exceeds the budget (default 500 ms). `loop.stats()` returns `barge_ins`,
  `last_response_latency_ms` (`None` until the first response) and
  `responses`.

```python
import asyncio

from plugins.baselithbot.voice import BufferedAudioPlayer, build_realtime_loop

player = BufferedAudioPlayer(sink=speaker.write)   # any async (bytes) sink
loop = build_realtime_loop(player)                 # None unless enabled
if loop is not None:
    run_task = asyncio.create_task(loop.run())
    async for frame in microphone_frames():        # e.g. SoundDeviceAudioBackend
        await loop.session.send_audio(frame)
```

`build_realtime_loop()` returns `None` when
`BASELITHBOT_VOICE_REALTIME_ENABLED` is off (the default) — the sequential
pipeline stays in charge and nothing realtime is constructed.

| Variable | Default | Purpose |
| -------- | ------- | ------- |
| `BASELITHBOT_VOICE_REALTIME_ENABLED`             | `false`                   | Select the realtime duplex loop over the sequential wake/STT/LLM/TTS pipeline |
| `BASELITHBOT_VOICE_REALTIME_MODEL`               | `gpt-4o-realtime-preview` | OpenAI Realtime model identifier |
| `BASELITHBOT_VOICE_REALTIME_VOICE`               | `alloy`                   | Assistant voice preset |
| `BASELITHBOT_VOICE_REALTIME_SILENCE_DURATION_MS` | `500`                     | Server-VAD silence window marking end of user speech (100–5000) |
| `BASELITHBOT_VOICE_REALTIME_LATENCY_BUDGET_MS`   | `500.0`                   | Warn when speech-stop to first assistant audio exceeds this budget |

A telephony bridge (SIP/Twilio media streams) is explicitly **future
work**: it would sit in front of this loop as another `DuplexVoiceSession`
transport and is deliberately not implemented here.

## Repository model

Baselithbot is **dual-hosted** but single-sourced:

- **Source of truth** — `plugins/baselithbot/` inside the `baselithcore`
  monorepo. All edits, bug fixes, and feature work land here first. The
  framework CI gates allowlist the plugin:
  [`scripts/check_official_plugin_typing.py`](https://github.com/baselithcore/baselithcore/blob/main/scripts/check_official_plugin_typing.py),
  [`scripts/check_architecture_boundaries.py`](https://github.com/baselithcore/baselithcore/blob/main/scripts/check_architecture_boundaries.py),
  plus `tests/plugins/baselithbot/` and
  `tests/unit/plugins_tests/test_baselithbot_*.py` — so every `core.*`
  change is immediately regression-tested against Baselithbot.
- **Publish target** — the standalone
  [`plugin-baselithbot`](https://github.com/baselithcore/plugin-baselithbot)
  repository. Marketplace consumers `pip install` from here. It is
  **output-only**: every release is a `git subtree split` from the
  monorepo, pushed with `--force-with-lease`. Never edit it directly.

Release flow summary:

```bash
cd baselithcore
# 1. Land changes in monorepo (PR + review as usual).
# 2. Rebuild UI bundle (ships only ui/dist/).
( cd plugins/baselithbot/ui && npm ci && npm run build )
# 3. Split and push.
git subtree split -P plugins/baselithbot -b baselithbot-split
git push --force-with-lease \
    git@github.com:baselithcore/plugin-baselithbot.git \
    baselithbot-split:main
git branch -D baselithbot-split
# 4. In the standalone repo: tag + `baselith plugin marketplace publish .`
# (or use the Backstage Scaffolder path).
```

The full pipeline — layout checklist, validator gates, Backstage
Scaffolder path — lives in
[`plugins/baselithbot/docs/publishing.md`](https://github.com/baselithcore/baselithcore/blob/main/plugins/baselithbot/docs/publishing.md).

## Where to look next

- Plugin-local README:
  [`plugins/baselithbot/README.md`](https://github.com/baselithcore/baselithcore/blob/main/plugins/baselithbot/README.md)
- Operations + security walkthroughs:
  [`plugins/baselithbot/docs/`](https://github.com/baselithcore/baselithcore/tree/main/plugins/baselithbot/docs)
- Publishing (manual + Scaffolder):
  [Backstage Publish](backstage-publish.md)
- Packaging rules for all plugins:
  [Packaging](packaging.md)

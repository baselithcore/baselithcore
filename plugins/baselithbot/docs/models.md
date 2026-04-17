# Model preferences

[← Index](./README.md)

`ModelPreferences` ([`model_config.py`](../model_config.py)) are the
operator-chosen `(provider, model)` pair plus optional failover chain.
Persisted atomically (`.tmp` + `os.replace`) to
`plugins/baselithbot/.state/model_preferences.json`.

## 1. Supported providers

### LLM (`LLMProvider`)

| Provider | Known models |
|----------|--------------|
| `openai` | `gpt-4o`, `gpt-4o-mini`, `gpt-4-turbo`, `gpt-4.1`, `gpt-5` |
| `anthropic` | `claude-opus-4-7`, `claude-sonnet-4-6`, `claude-haiku-4-5-20251001`, `claude-3-5-sonnet-20241022` |
| `ollama` | `llama3.2`, `llama3.1`, `mistral:latest`, `qwen2.5`, `phi3` |
| `huggingface` | `meta-llama/Llama-3.1-8B-Instruct`, `mistralai/Mistral-7B-Instruct-v0.3` |

### Vision (`VisionProvider`)

| Provider | Known models |
|----------|--------------|
| `openai` | `gpt-4o`, `gpt-4o-mini`, `gpt-4-turbo` |
| `anthropic` | `claude-3-5-sonnet-20241022`, `claude-opus-4-7`, `claude-sonnet-4-6` |
| `google` | `gemini-2.0-flash`, `gemini-1.5-pro` |
| `ollama` | `llava`, `llava:13b`, `bakllava` |

## 2. `ModelPreferences` schema

| Field | Type | Default | Constraint |
|-------|------|---------|------------|
| `provider` | `LLMProvider` | `"ollama"` | Literal |
| `model` | str | `"llama3.2"` | 1–120 chars, non-blank |
| `temperature` | float | `0.7` | 0.0–2.0 |
| `max_tokens` | int \| null | `None` | 1–200_000 |
| `vision_provider` | `VisionProvider` | `"openai"` | Literal |
| `vision_model` | str | `"gpt-4o"` | 1–120 chars, non-blank |
| `failover_chain` | list[`FailoverEntry`] | `[]` | Ordered |

### `FailoverEntry`

```json
{"provider": "openai", "model": "gpt-4o", "cooldown_seconds": 30.0}
```

`cooldown_seconds` clamped to 0.0–3600.0.

## 3. Security model

- API keys **never** returned by `snapshot()` or persisted to disk. They
  stay in env vars under `core.config.services`.
- Writes bounded to catalog (`KNOWN_PROVIDERS` / `KNOWN_VISION_PROVIDERS`);
  unknown provider → 422.
- Persistence atomic (`.tmp` + `os.replace`), restricted to plugin-owned
  `.state/` directory; no cross-plugin writes.
- Thread-safe via `threading.Lock`.

## 4. Dashboard endpoints

```http
GET /baselithbot/dash/models
```

Returns:

```json
{
  "current": {
    "provider": "anthropic",
    "model": "claude-sonnet-4-6",
    "temperature": 0.7,
    "max_tokens": null,
    "vision_provider": "openai",
    "vision_model": "gpt-4o",
    "failover_chain": [...]
  },
  "options": {
    "llm_providers": {...},
    "vision_providers": {...}
  }
}
```

Write (🔒 5/min):

```http
PUT /baselithbot/dash/models
Content-Type: application/json
Authorization: Bearer <BASELITHBOT_DASHBOARD_TOKEN>

{
  "provider": "anthropic",
  "model": "claude-sonnet-4-6",
  "temperature": 0.5,
  "vision_provider": "openai",
  "vision_model": "gpt-4o",
  "failover_chain": [
    {"provider": "openai", "model": "gpt-4o", "cooldown_seconds": 30.0},
    {"provider": "ollama", "model": "llama3.2", "cooldown_seconds": 5.0}
  ]
}
```

Emits `models.updated` on the SSE bus.

## 5. Application semantics

Changes apply **on the next agent startup** — running tasks keep the
model they started with to avoid mid-task churn. Therefore the operator
flow is:

1. PUT new prefs.
2. Wait for in-flight runs to finish (`/dash/run-task/latest`).
3. Shut down + restart agent (automatic on process restart, or via
   `/status` + explicit restart).

## 6. Failover chain behaviour

When a provider in the primary slot fails (rate limit, auth error, 5xx),
`core.services` consults the chain top-to-bottom. Each entry has a
`cooldown_seconds` window during which that entry is skipped after a
failure. No automatic re-ordering — list is the operator's intent.

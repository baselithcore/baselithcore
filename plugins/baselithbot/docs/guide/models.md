# Models

Operator-chosen LLM and Vision provider/model preferences, plus the
encrypted per-provider API key vault.

## What the page does

- **Provider + model pickers** for both the reasoning LLM (`openai`,
  `anthropic`, `ollama`, `huggingface`) and the Vision model used for
  screenshot understanding (`openai`, `anthropic`, `google`, `ollama`),
  each bounded to a known-model catalog so a write can't smuggle an
  arbitrary string downstream.
- **Temperature** (0.0–2.0) and optional **max tokens** (1–200,000).
- **Failover chain** — an ordered list of `{provider, model,
  cooldown_seconds}` fallback entries tried top-to-bottom when the primary
  provider fails (rate limit, auth error, 5xx); each entry is skipped for
  its cooldown window after a failure, no automatic re-ordering.
- **Provider key editor** — set, mask-preview (`***<last4>`), test, or
  remove an API key per provider. Keys are never echoed back in plaintext.

## Backend

`GET /dash/models` returns current preferences plus the allowed option
catalog; `PUT /dash/models` (🔒, 5/min) validates and persists, emitting
`models.updated` on the SSE bus. `GET/PUT/DELETE /dash/provider-keys/{provider}`
(write 🔒) and `POST /dash/provider-keys/{provider}/test` (🔒) manage the
encrypted key vault.

## Notes

Preference changes apply on the **next agent startup** — an in-flight run
keeps the model it started with. Persistence is atomic
(`plugins/baselithbot/.state/model_preferences.json`, `.tmp` + `os.replace`)
and thread-safe. Provider keys are encrypted at rest — see
[Security & RBAC](../reference/security.md).

# BaselithCore TypeScript SDK

A small, typed client for the [BaselithCore](https://baselithcore.xyz) API. Built
on the platform `fetch` — **zero runtime dependencies** — and runs in Node 18+,
browsers, and edge runtimes. Retries, idempotency keys, streaming, cursor
pagination, and a typed error hierarchy. Covers chat, streaming chat, feedback,
health and readiness, async agent runs, run event streams and history,
human-in-the-loop approvals, and webhooks.

## Install

```bash
npm install baselith-sdk
```

## Quick start

```ts
import { BaselithClient } from "baselith-sdk";

const client = new BaselithClient({
  baseUrl: "https://api.example.com",
  apiKey: "sk-...",
});

const res = await client.chat("What is BaselithCore?");
console.log(res.answer);

// Streaming
for await (const chunk of client.chatStream("Tell me a story")) {
  process.stdout.write(chunk);
}

// Feedback (Idempotency-Key auto-generated)
await client.submitFeedback({
  query: "What is BaselithCore?",
  answer: res.answer,
  feedback: "positive",
});
```

### Async agent runs

```ts
import { RunTimeoutError } from "baselith-sdk";

const sub = await client.submitAgentRun("Summarise the Q3 report"); // POST /v1/agent/async
try {
  const status = await client.waitForRun(sub.task_id, { timeoutMs: 120_000, pollIntervalMs: 2_000 });
  console.log(status.status, status.result); // completed / failed / cancelled
} catch (e) {
  if (e instanceof RunTimeoutError) console.log("still running:", e.lastStatus);
  else throw e;
}
```

### Run events, history and approvals (admin Basic credentials)

```ts
const ops = new BaselithClient({
  baseUrl: "https://api.example.com",
  basicAuth: { username: "admin", password: "pw" },
});

// Structured agent events over SSE; ends after a final/error/human event.
for await (const event of ops.streamRunEvents("run-123")) {
  console.log(event.id, event.type, event.content);
}

const history = await ops.getRunHistory("run-123", { limit: 20 });

for await (const page of ops.iterPages((o) => ops.listApprovals(o))) {
  for (const pending of page.pending) {
    await ops.decideApproval(pending.run_id, { approved: true, reason: "reviewed" });
    await ops.resumeRun(pending.run_id);
  }
}
```

Subscribe before starting or resuming the run: the feed is not replayed
(`lastEventId` is sent as `Last-Event-ID` but cannot rewind it).

### Webhooks

```ts
const created = await client.createWebhook({
  url: "https://hooks.example.com/baselith",
  event_types: ["agent.completed", "agent.failed"],
});
console.log(created.secret); // returned only once
await client.listWebhooks({ limit: 50 });
await client.deleteWebhook(created.endpoint.id);
for await (const page of client.iterPages((o) => client.listWebhookDeliveries(o), { limit: 100 })) {
  for (const d of page.deliveries) if (d.status === "failed") await client.replayWebhookDelivery(d.id);
}
```

### Pagination

List methods return one page (`next_cursor`, `has_more`, plus the endpoint's
list key) and take `{ limit, cursor }`. `client.iterPages(fetch, opts)` — or
the standalone `iterPages` export — follows `next_cursor` to the end.

## Authentication

Pass one credential in the constructor:

- `apiKey` → sent as the `x-api-key` header, or
- `bearerToken` → `Authorization: Bearer <token>` (self-issued **or** OIDC SSO
  tokens).

The runs and approvals routes use the admin HTTP Basic credentials:
`basicAuth: { username, password }` (cannot be combined with `bearerToken`).

## Options

| Option        | Default | Description                                  |
| ------------- | ------- | -------------------------------------------- |
| `baseUrl`     | —       | API base URL (required)                      |
| `apiKey`      | —       | API key (`x-api-key`)                         |
| `bearerToken` | —       | Bearer/OIDC token                            |
| `basicAuth`   | —       | `{ username, password }` for HTTP Basic      |
| `tenantId`    | —       | Sent as `X-Tenant-ID`                         |
| `apiVersion`  | `"v1"`  | Path prefix; `null` for unversioned paths    |
| `timeoutMs`   | `30000` | Per-request timeout                          |
| `maxRetries`  | `2`     | Retries on 429/5xx with backoff + jitter     |
| `fetchImpl`   | global  | Inject a custom `fetch` (testing/proxies)    |

`timeoutMs` bounds the wait for the response headers only, so a long SSE
stream is never cut by it. `decideApproval`, `resumeRun` and
`replayWebhookDelivery` are non-idempotent: they always send an
`Idempotency-Key` (pass `{ idempotencyKey }` to choose it) and are retried only
when the request never left the client (`ApiConnectionError.notSent`, e.g.
`ECONNREFUSED`) or got a `429` — never after a timeout or a `5xx`, when the
server may still be executing them. Retry those yourself with the same key.
`resumeRun` runs the resumed agent loop in-request and waits up to
`timeoutMs: 660_000` by default. Breaking out of `chatStream` /
`streamRunEvents` cancels the response body, closing the connection.

## Errors

API failures throw a subclass of `BaselithApiError`
(`AuthenticationError`, `PermissionDeniedError`, `NotFoundError`,
`RateLimitError`, `ServerError`) carrying `statusCode`, `code`, `message`, and
`requestId` parsed from the server's error envelope. Network failures throw
`ApiConnectionError`; `waitForRun` throws `RunTimeoutError`.

```ts
import { RateLimitError } from "baselith-sdk";

try {
  await client.chat("hi");
} catch (e) {
  if (e instanceof RateLimitError) console.log("retry after", e.retryAfter);
}
```

## Development

```bash
npm install
npm run typecheck
npm test
npm run build   # -> dist/ (ESM + CJS + .d.ts)
```

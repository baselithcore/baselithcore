# BaselithCore TypeScript SDK

A small, typed client for the [BaselithCore](https://baselithcore.xyz) API. Built
on the platform `fetch` — **zero runtime dependencies** — and runs in Node 18+,
browsers, and edge runtimes. Retries, idempotency keys, streaming, and a typed
error hierarchy.

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

## Authentication

Pass one credential in the constructor:

- `apiKey` → sent as the `x-api-key` header, or
- `bearerToken` → `Authorization: Bearer <token>` (self-issued **or** OIDC SSO
  tokens).

## Options

| Option        | Default | Description                                  |
| ------------- | ------- | -------------------------------------------- |
| `baseUrl`     | —       | API base URL (required)                      |
| `apiKey`      | —       | API key (`x-api-key`)                         |
| `bearerToken` | —       | Bearer/OIDC token                            |
| `tenantId`    | —       | Sent as `X-Tenant-ID`                         |
| `apiVersion`  | `"v1"`  | Path prefix; `null` for unversioned paths    |
| `timeoutMs`   | `30000` | Per-request timeout                          |
| `maxRetries`  | `2`     | Retries on 429/5xx with backoff + jitter     |
| `fetchImpl`   | global  | Inject a custom `fetch` (testing/proxies)    |

## Errors

API failures throw a subclass of `BaselithApiError`
(`AuthenticationError`, `PermissionDeniedError`, `NotFoundError`,
`RateLimitError`, `ServerError`) carrying `statusCode`, `code`, `message`, and
`requestId` parsed from the server's error envelope. Network failures throw
`ApiConnectionError`.

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

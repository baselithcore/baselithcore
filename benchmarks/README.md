# Benchmarks

Microbenchmarks for the small, hot, in-process primitives BaselithCore runs on
nearly every request. No network, database, or LLM calls — results are
deterministic and reproducible anywhere.

## Run

```bash
python benchmarks/run.py             # human-readable table
python benchmarks/run.py --markdown  # Markdown table (for docs)
```

## What is measured

| Operation | Why it's hot |
| --------- | ------------ |
| Prompt render (`{{var}}`) | Every templated LLM call |
| Scope match (wildcard) | Every authorized request |
| Webhook HMAC sign | Every webhook delivery |
| Cursor paginate | Every list endpoint page |
| Per-tenant key derive (HKDF) | Per-tenant field encryption setup |
| Field encrypt+decrypt (AES-GCM) | Encryption-at-rest read/write |
| `orjson` vs stdlib `json` | Response serialization |

## Methodology & caveats

Each case runs a warm-up call, then a fixed iteration count timed with
`time.perf_counter`; throughput is `iterations / elapsed`. Numbers are
**single-machine and indicative** — run-to-run variance is a few percent. Use
them for relative comparison (e.g. `orjson` vs stdlib) and regression spotting,
not as absolute guarantees. Reproduce on your own hardware before quoting.

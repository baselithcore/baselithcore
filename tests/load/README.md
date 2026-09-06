# Load testing

A [Locust](https://locust.io) profile that drives the public API paths (health,
chat, feedback) at configurable concurrency. It runs against a **running**
instance — local, staging, or a dedicated load environment — not as part of the
unit suite.

## Install

```bash
pip install -e ".[load]"
```

## Run

Interactive (web UI at <http://localhost:8089>):

```bash
BASELITH_API_KEY=sk-... locust -f tests/load/locustfile.py --host http://localhost:8000
```

Headless smoke (50 users, ramp 10/s, 30s):

```bash
BASELITH_API_KEY=sk-... locust -f tests/load/locustfile.py \
  --host http://localhost:8000 --headless -u 50 -r 10 -t 30s
```

## Environment

| Variable             | Default | Purpose                                   |
| -------------------- | ------- | ----------------------------------------- |
| `BASELITH_API_KEY`   | —       | Sent as `X-API-Key` for authed paths      |
| `BASELITH_API_PREFIX`| `/v1`   | Version prefix for chat/feedback          |
| `BASELITH_PERF_SKIP_CHAT` | —  | Skip the chat task where no LLM is reachable |

## Performance budget

`scripts/check_perf_budget.py` turns a run into a verdict. Locust's own exit
code only reports request failures, so a run that made **zero** requests — a
backend that never came up — used to look exactly like a pass, and a run 50x
slower still exited 0.

```bash
locust -f tests/load/locustfile.py --host http://localhost:8000 \
  --headless -u 10 -r 5 -t 60s --only-summary --csv perf
python scripts/check_perf_budget.py --stats perf_stats.csv --report
```

The gate checks the run made at least `min_total_requests`, that every budgeted
endpoint appears with its own floor, and that each is within its `p95_ms` and
failure-ratio budget. An endpoint exercised with no budget also fails, so a task
added here cannot slip through unmeasured.

Budgets live in `scripts/perf_budget.json` and are **ceilings set deliberately**
— there is no `--update-baseline`, because a budget rewritten from the run that
broke it enforces nothing. They carry wide headroom (a shared CI runner is
noisy): they catch a dead backend or a 10x regression, not a 10% drift.

The task weights (health 5 : chat 10 : feedback 2) approximate a chat-heavy
workload; adjust in `locustfile.py` to match your traffic mix.

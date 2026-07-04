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

The task weights (health 5 : chat 10 : feedback 2) approximate a chat-heavy
workload; adjust in `locustfile.py` to match your traffic mix.

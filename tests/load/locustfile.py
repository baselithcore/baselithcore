"""
Locust load profile for the BaselithCore API.

Drives the public request paths — health probes, chat, and feedback — at
configurable concurrency so you can measure latency and throughput against a
running instance (local, staging, or a load environment). It is **not** part of
the unit suite; run it explicitly against a deployed target:

    pip install -e ".[load]"
    BASELITH_API_KEY=sk-... \\
      locust -f tests/load/locustfile.py --host http://localhost:8000

    # Headless smoke (50 users, 30s):
    BASELITH_API_KEY=sk-... locust -f tests/load/locustfile.py \\
      --host http://localhost:8000 --headless -u 50 -r 10 -t 30s

Set ``BASELITH_API_KEY`` for authenticated paths; without it only the public
health probe is exercised meaningfully.
"""

from __future__ import annotations

import os
import uuid

from locust import HttpUser, between, task

_API_KEY = os.getenv("BASELITH_API_KEY", "")
_API_PREFIX = os.getenv("BASELITH_API_PREFIX", "/v1")


class BaselithUser(HttpUser):
    """A synthetic API client mixing health, chat, and feedback traffic."""

    # Think-time between tasks so the profile resembles real usage.
    wait_time = between(0.5, 2.5)

    def on_start(self) -> None:
        self._headers = {"Content-Type": "application/json"}
        if _API_KEY:
            self._headers["X-API-Key"] = _API_KEY
        self._conversation_id = str(uuid.uuid4())

    @task(5)
    def health(self) -> None:
        # Liveness probe is unauthenticated and unversioned.
        self.client.get("/health", name="GET /health")

    @task(10)
    def chat(self) -> None:
        self.client.post(
            f"{_API_PREFIX}/chat",
            json={
                "query": "What is BaselithCore?",
                "conversation_id": self._conversation_id,
            },
            headers=self._headers,
            name="POST /chat",
        )

    @task(2)
    def feedback(self) -> None:
        self.client.post(
            f"{_API_PREFIX}/feedback",
            json={
                "query": "What is BaselithCore?",
                "answer": "An orchestration engine for agentic AI.",
                "feedback": "positive",
                "conversation_id": self._conversation_id,
            },
            headers=self._headers,
            name="POST /feedback",
        )

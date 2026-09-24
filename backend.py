import os

import uvicorn
from dotenv import load_dotenv

# Before anything imports torch or numpy: their thread pools are sized once,
# at first import, and N workers each taking every core oversubscribe the host.
from core.config.concurrency import share_cpu_threads

share_cpu_threads()

from core.api.factory import create_app  # noqa: E402
from core.config import get_app_config, get_core_config  # noqa: E402
from core.observability.logging import get_logger  # noqa: E402

load_dotenv()

_app_config = get_app_config()
logger = get_logger(__name__)

# === FastAPI app ===
app = create_app()

HOST = _app_config.host
PORT = _app_config.port


def _optional_int(raw: str | None) -> int | None:
    """``int`` of a non-empty env value, else ``None`` (uvicorn's "unset")."""
    return int(raw) if raw and raw.strip() else None


# === Direct startup (if not running uvicorn from CLI) ===
if __name__ == "__main__":
    core_config = get_core_config()
    logger.info(
        "🌐 Starting FastAPI backend on %s:%s (debug=%s).",
        HOST,
        PORT,
        core_config.debug,
    )
    uvicorn.run(
        "backend:app",
        host=HOST,
        port=PORT,
        reload=core_config.debug,
        # Mirror the container CMD (see Dockerfile). Without proxy_headers,
        # request.client.host behind a load balancer is the proxy for every
        # caller, collapsing the per-IP rate limiter, the failed-auth throttle
        # and the admin lockout into ONE shared bucket. Trust stays limited to
        # FORWARDED_ALLOW_IPS (uvicorn's own default is 127.0.0.1) — widen it
        # only to your LB/ingress address(es), never to "*".
        proxy_headers=True,
        forwarded_allow_ips=os.getenv("FORWARDED_ALLOW_IPS", "127.0.0.1"),
        # Bounded drain so SIGTERM with open SSE streams still runs lifespan
        # cleanup before the supervisor SIGKILLs (k8s default grace: 30s).
        # Same env knob as the container CMD, so tuning it once covers both.
        timeout_graceful_shutdown=int(os.getenv("GRACEFUL_SHUTDOWN_TIMEOUT", "30")),
        # Same two knobs the container CMD honours, so `python backend.py`
        # behind the same proxy behaves the same. uvicorn's own 5s keep-alive
        # is shorter than every common proxy's upstream idle timeout (nginx,
        # ALB, Envoy: 60s), so the proxy reused sockets this process had
        # already closed and surfaced sporadic 502s — the image fixed that
        # with 75s, and this entry point did not. UVICORN_LIMIT_CONCURRENCY
        # is load-shedding: above that many concurrent connections/tasks
        # uvicorn answers 503 at once instead of queueing until the client
        # or proxy times out; unset keeps uvicorn's default (no limit).
        timeout_keep_alive=int(os.getenv("UVICORN_KEEP_ALIVE", "75")),
        limit_concurrency=_optional_int(os.getenv("UVICORN_LIMIT_CONCURRENCY")),
    )

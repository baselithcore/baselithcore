import os

import uvicorn
from dotenv import load_dotenv

from core.api.factory import create_app
from core.config import get_app_config, get_core_config
from core.observability.logging import get_logger

load_dotenv()

_app_config = get_app_config()
logger = get_logger(__name__)

# === FastAPI app ===
app = create_app()

HOST = _app_config.host
PORT = _app_config.port

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
    )

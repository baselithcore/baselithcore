from core.api.factory import create_app
from core.config import get_app_config, get_core_config
from core.observability.logging import get_logger
import uvicorn

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
    uvicorn.run("backend:app", host=HOST, port=PORT, reload=core_config.debug)

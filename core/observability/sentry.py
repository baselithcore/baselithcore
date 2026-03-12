"""
Sentry Error Tracking Integration.

Initializes the Sentry SDK for capturing unhandled exceptions
and monitoring performance if a SENTRY_DSN is provided in the configuration.
"""

import sentry_sdk
from core.observability.logging import get_logger
from core.config import get_app_config

logger = get_logger(__name__)


def init_sentry() -> None:
    """
    Initialize Sentry SDK for error and performance tracking.
    This should be called early in the application startup phase.
    """
    config = get_app_config()

    sentry_dsn = getattr(config, "sentry_dsn", None)

    if sentry_dsn:
        try:
            sentry_sdk.init(
                dsn=sentry_dsn,
                # Set traces_sample_rate to 1.0 to capture 100% of transactions for performance monitoring.
                traces_sample_rate=1.0,
                # Set profiles_sample_rate to 1.0 to profile 100% of sampled transactions.
                profiles_sample_rate=1.0,
            )
            logger.info("Sentry SDK initialized successfully for error tracking.")
        except Exception as e:
            logger.error(f"Failed to initialize Sentry SDK: {e}", exc_info=True)
    else:
        logger.debug("SENTRY_DSN not configured. Sentry tracking is disabled.")

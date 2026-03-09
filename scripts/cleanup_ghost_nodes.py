import logging
from core.observability.logging import get_logger
import sys
import os

# Add project root to sys.path
sys.path.append(os.getcwd())

from core.graph import graph_db

logging.basicConfig(level=logging.INFO)
logger = get_logger("cleanup")


def cleanup_ghost_nodes():
    if not graph_db.is_enabled():
        logger.warning("GraphDB not enabled.")
        return

    logger.info("Checking for ghost nodes (nodes without labels)...")

    # query to find nodes with no labels
    # Note: syntax might depend on Cypher version, but usually size(labels(n)) works.
    try:
        query_check = "MATCH (n) WHERE size(labels(n)) = 0 RETURN count(n)"
        res = graph_db.query(query_check)

        # res is likely [[count], [header?]] or similar raw structure
        # Log the raw response to be sure, then robustly extract int
        logger.info(f"Raw response: {res}")

        count = 0
        if res and isinstance(res, list):
            # Recursively find the first integer
            def find_int(x):
                if isinstance(x, int) and not isinstance(x, bool):
                    return x
                if isinstance(x, list):
                    for item in x:
                        val = find_int(item)
                        if val is not None:
                            return val
                return None

            found = find_int(res)
            if found is not None:
                count = found

        logger.info(f"Found {count} ghost nodes.")

        if count > 0:
            logger.info("Deleting ghost nodes...")
            query_delete = "MATCH (n) WHERE size(labels(n)) = 0 DETACH DELETE n"
            graph_db.query(query_delete)
            logger.info("Ghost nodes deleted.")
        else:
            logger.info("Graph is clean.")

    except Exception as e:
        logger.error(f"Error during cleanup: {e}", exc_info=True)


if __name__ == "__main__":
    cleanup_ghost_nodes()

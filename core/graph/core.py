"""
Core Graph Database Integration.

Provides the primary interface for managing relational knowledge using
FalkorDB/RedisGraph. Orchestrates connections, query execution with
TTL-based caching, and delegates specialized domain operations to
sub-modules.
"""

from __future__ import annotations

from core.observability import get_logger
from typing import Any, Mapping, Sequence, Optional, Dict

from core.config import get_storage_config
from core.cache import TTLCache, RedisTTLCache, create_redis_client

# Import specialized modules
# Import specialized modules
from . import query_builder, operations, linking, code_graph, retrieval

try:  # pragma: no cover - optional dependency
    from redis import Redis
except Exception:  # pragma: no cover - redis not available
    Redis = None  # type: ignore[assignment,misc]

logger = get_logger(__name__)


class GraphDb:
    """
    Client for FalkorDB-powered knowledge graphs.

    Manages the lifecycle of graph interactions, including configuration
    loading, connection pooling, and result caching. Acts as a unified
    entry point for graph operations ranging from generic node/edge
    management to specialized code analysis and documentation linking.
    """

    def __init__(
        self,
        *,
        enabled: bool | None = None,
        url: str | None = None,
        graph_name: str | None = None,
        timeout: float | None = None,
    ) -> None:
        # Load config lazily
        config = get_storage_config()

        self.enabled = enabled if enabled is not None else config.graph_db_enabled
        self.graph_name = graph_name or config.graph_db_name
        self._url = url or config.graph_db_url
        self._timeout = timeout if timeout is not None else config.graph_db_timeout
        self._cache_ttl = config.graph_cache_ttl
        self._client: "Redis | None" = None
        self._cache: Optional[Any] = None
        self._cache_initialized = False

    def _ensure_cache_initialized(self) -> None:
        """Ensure cache is initialized (lazily)."""
        if self._cache_initialized:
            return

        self._cache_initialized = True
        if not self.enabled:
            return

        config = get_storage_config()
        try:
            if config.cache_backend == "redis":
                redis_client = create_redis_client(config.cache_redis_url)
                self._cache = RedisTTLCache(
                    redis_client,
                    prefix=f"{config.cache_redis_prefix}:graph",
                    default_ttl=self._cache_ttl,
                )
            else:
                self._cache = TTLCache(maxsize=1024, ttl=self._cache_ttl)
        except Exception as e:
            logger.warning(f"[graphdb] Failed to initialize cache: {e}")

    def is_enabled(self) -> bool:
        """Check if graph database is enabled."""
        return bool(self.enabled)

    def ping(self) -> bool:
        """
        Verify backend reachability without altering state.

        Returns:
            True if backend is reachable, False otherwise
        """
        if not self.enabled:
            return False
        try:
            self._get_client().ping()
            return True
        except Exception as exc:  # pragma: no cover - diagnostic log
            logger.warning("[graphdb] ping failed: %s", exc)
            return False

    def create_constraints(self) -> None:
        """
        Create uniqueness constraints for main labels.
        Idempotent operation (errors are handled/ignored if constraint already exists).
        """
        if not self.enabled:
            return

        labels_fields = [
            ("Document", "id"),
            ("UserStory", "id"),
            ("Epic", "id"),
            ("TestCase", "id"),
            ("Requirement", "id"),
            ("Topic", "name"),
        ]

        client = self._get_client()
        for label, field in labels_fields:
            # 1. Create index (idempotent)
            try:
                client.execute_command(
                    "GRAPH.QUERY",
                    self.graph_name,
                    f"CREATE INDEX FOR (n:{label}) ON (n.{field})",
                )
            except Exception as e:
                # If index already exists, might give error. Ignore if "already indexed".
                msg = str(e)
                if "already indexed" not in msg and "already exists" not in msg:
                    logger.warning(
                        f"[graphdb] Error creating index {label}({field}): {msg}"
                    )

            # 2. Create constraint (idempotent)
            try:
                client.execute_command(
                    "GRAPH.CONSTRAINT",
                    "CREATE",
                    self.graph_name,
                    "UNIQUE",
                    "NODE",
                    label,
                    "PROPERTIES",
                    1,
                    field,
                )
                logger.info(f"[graphdb] Constraint created/verified: {label}({field})")
            except Exception as e:
                # E.g. "Constraint already exists"
                msg = str(e)
                if "already exists" not in msg:
                    logger.warning(
                        f"[graphdb] Error creating constraint {label}: {msg}"
                    )

    def query(self, cypher: str, params: Mapping[str, Any] | None = None) -> list[Any]:
        """
        Execute a Cypher query and return the raw Redis payload.

        Uses parameters with CYPHER prefix to avoid unsafe string concatenation.
        Returns empty list if graph is disabled.

        Args:
            cypher: Cypher query string
            params: Optional query parameters

        Returns:
            Query result list
        """
        if not self.enabled:
            logger.debug("[graphdb] query ignored because disabled")
            return []

        from core.context import get_current_tenant_id

        current_tenant = get_current_tenant_id()

        # Merge tenant_id into params safely
        safe_params = dict(params) if params else {}
        if "tenant_id" not in safe_params:
            safe_params["tenant_id"] = current_tenant

        self._ensure_cache_initialized()

        # Cache Check (Read-Only Heuristic)
        # Only cache if query doesn't modify data
        upper_cypher = cypher.upper()
        is_read_only = not any(
            kw in upper_cypher
            for kw in [
                "CREATE",
                "MERGE",
                "SET",
                "DELETE",
                "DETACH",
                "REMOVE",
                "DROP",
                "CALL",
            ]
        )

        cache_key = None
        if is_read_only and self._cache:
            # Create a deterministic key from cypher and params
            key_parts = [cypher]
            if params:
                for k in sorted(params.keys()):
                    key_parts.append(f"{k}={params[k]}")
            cache_key = "|".join(key_parts)

            cached = self._cache.get(cache_key)
            if cached is not None:
                # logger.debug("[graphdb] cache hit") # verbose
                return cached

        query_text = query_builder.build_query(cypher, safe_params)
        client = self._get_client()
        result = client.execute_command(  # type: ignore[union-attr]
            "GRAPH.QUERY",
            self.graph_name,
            query_text,
            "--compact",
        )

        # Cache Set
        if is_read_only and self._cache and cache_key:
            self._cache.set(cache_key, result)

        return result

    # --- Node & Edge Operations (delegated to operations module) ---

    def get_node(self, node_id: str) -> Optional[Dict[str, Any]]:
        """Retrieve node properties by ID."""
        if not self.enabled:
            return None
        return operations.get_node(self.query, node_id)

    def upsert_node(
        self,
        node_id: str,
        *,
        labels: Sequence[str] | None = None,
        properties: Mapping[str, Any] | None = None,
    ) -> None:
        """Create or update a node with stable ID and mergeable properties."""
        if not self.enabled:
            return
        operations.upsert_node(
            self.query, node_id, labels=labels, properties=properties
        )

    def upsert_edge(
        self,
        source_id: str,
        relationship: str,
        target_id: str,
        *,
        properties: Mapping[str, Any] | None = None,
    ) -> None:
        """Create or update a directed relationship between two nodes."""
        if not self.enabled:
            return
        operations.upsert_edge(
            self.query, source_id, relationship, target_id, properties=properties
        )

    def delete_node(self, node_id: str) -> None:
        """Delete a node and all its incident relationships."""
        if not self.enabled:
            return
        operations.delete_node(self.query, node_id)

    def delete_orphan_nodes(self) -> int:
        """
        Delete orphan nodes (without relationships) from the graph.
        Explicitly excludes Document nodes for safety.

        Returns:
            Number of nodes deleted
        """
        if not self.enabled:
            return 0
        return operations.delete_orphan_nodes(self.query)

    # --- Linking Operations (delegated to linking module) ---

    def link_story_to_doc(
        self,
        story_id: str,
        doc_id: str,
        *,
        title: Optional[str] = None,
        priority: Optional[str] = None,
        status: Optional[str] = None,
    ) -> None:
        """Link a user story to a knowledge base document."""
        if not self.enabled:
            return
        linking.link_story_to_doc(
            self.upsert_node,
            self.upsert_edge,
            story_id,
            doc_id,
            title=title,
            priority=priority,
            status=status,
        )

    def link_node_to_external_issue(
        self,
        source_id: str,
        *,
        issue_key: Optional[str] = None,
        issue_status: Optional[str] = None,
        issue_url: Optional[str] = None,
        issue_source: str = "external",
    ) -> None:
        """Link a node to an external issue (from any issue tracker)."""
        if not self.enabled:
            return
        linking.link_node_to_external_issue(
            self.upsert_node,
            self.upsert_edge,
            source_id,
            issue_key=issue_key,
            issue_status=issue_status,
            issue_url=issue_url,
            issue_source=issue_source,
        )

    def get_linked_external_issues(
        self, node_id: str, issue_source: Optional[str] = None
    ) -> list[dict[str, Any]]:
        """
        Retrieve external issues linked to a specific node.

        Args:
            node_id: Node identifier
            issue_source: Optional filter by source (e.g., "tracker", "github")

        Returns:
            List of dicts with keys: key, status, url, source
        """
        if not self.enabled:
            return []
        return linking.get_linked_external_issues(self.query, node_id, issue_source)

    def get_document_subgraph(self, doc_id: str) -> dict[str, Any]:
        """
        Retrieve the subgraph (neighborhood) for a given document.

        Args:
            doc_id: The document ID (or KB path).

        Returns:
            Dict with 'nodes' and 'links' for visualization.
        """
        if not self.enabled:
            return {"nodes": [], "links": []}
        return retrieval.get_subgraph_for_node(self.query, doc_id)

    def search_node(self, prop: str, value: str) -> Optional[str]:
        """Cerca un nodo per proprietà."""
        if not self.enabled:
            return None
        return retrieval.search_node_by_property(self.query, prop, value)

    def record_document_feedback(
        self, document_id: str, feedback: str, comment: Optional[str] = None
    ) -> None:
        """
        Update feedback counters on a Document node.

        Args:
            document_id: Document identifier
            feedback: Feedback sentiment ("positive" or "negative")
            comment: Optional feedback comment
        """
        if not self.enabled:
            return
        linking.record_document_feedback(self.query, document_id, feedback, comment)

    # --- Code Graph Operations (delegated to code_graph module) ---

    def upsert_code_node(
        self,
        node_id: str,
        label: str,
        name: str,
        file_path: str,
        *,
        properties: Mapping[str, Any] | None = None,
    ) -> None:
        """Create or update a node representing a code entity (File, Class, Function)."""
        if not self.enabled:
            return
        code_graph.upsert_code_node(
            self.upsert_node, node_id, label, name, file_path, properties=properties
        )

    def upsert_code_relation(
        self,
        source_id: str,
        relation_type: str,
        target_id: str,
    ) -> None:
        """Create a relationship between two code components (e.g., DEFINES, CONTAINS)."""
        if not self.enabled:
            return
        code_graph.upsert_code_relation(
            self.upsert_edge, source_id, relation_type, target_id
        )

    # --- Internals ---

    def close(self) -> None:
        """Close the Redis connection if it exists."""
        if self._client:
            try:
                self._client.close()
            except Exception as e:
                logger.warning(f"[graphdb] Error closing connection: {e}")
            finally:
                self._client = None

    def _get_client(self) -> "Redis":
        """Get or create Redis client connection."""
        if not self.enabled:
            raise RuntimeError("GraphDB not enabled (GRAPH_DB_ENABLED=false)")
        if Redis is None:
            raise RuntimeError(
                "GraphDB requires the redis package; install it to enable."
            )
        if self._client is None:
            self._client = Redis.from_url(
                self._url,
                socket_timeout=self._timeout,
                decode_responses=True,
            )
        return self._client


# Singleton instance, ready to use but lazy: doesn't open connection until needed.
graph_db = GraphDb()

__all__ = ["GraphDb", "graph_db"]

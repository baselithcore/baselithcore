"""
Relational Knowledge Linking Helpers.

Implements domain-agnostic patterns for connecting disparate knowledge
entities. Supports cross-referencing between user stories, documentation
artifacts, and external issue trackers to build a cohesive trace
of system intent and status.
"""

from __future__ import annotations

from core.observability import get_logger
from typing import Any, Optional, Callable
from datetime import datetime, timezone

logger = get_logger(__name__)


def link_story_to_doc(
    upsert_node_fn: Callable,
    upsert_edge_fn: Callable,
    story_id: str,
    doc_id: str,
    *,
    title: Optional[str] = None,
    priority: Optional[str] = None,
    status: Optional[str] = None,
) -> None:
    """
    Link a user story to a knowledge base document (Story DERIVES_FROM Document).

    Args:
        upsert_node_fn: Node upsert function
        upsert_edge_fn: Edge upsert function
        story_id: Story identifier
        doc_id: Document identifier
        title: Optional story title
        priority: Optional story priority
        status: Optional story status
    """
    props: dict[str, Any] = {}
    if title:
        props["title"] = title
    if priority:
        props["priority"] = priority
    if status:
        props["status"] = status
    if props:
        upsert_node_fn(story_id, labels=["Story"], properties=props)
    upsert_edge_fn(story_id, "DERIVES_FROM", doc_id)


def link_node_to_external_issue(
    upsert_node_fn: Callable,
    upsert_edge_fn: Callable,
    source_id: str,
    *,
    issue_key: Optional[str] = None,
    issue_status: Optional[str] = None,
    issue_url: Optional[str] = None,
    issue_source: str = "external",
) -> None:
    """
    Link a node to an external issue tracker issue.

    This is a generic function that can be used to link to any external
    issue tracking system.

    Args:
        upsert_node_fn: Node upsert function
        upsert_edge_fn: Edge upsert function
        source_id: Source node identifier
        issue_key: External issue key (e.g., "PROJ-123", "GH-456")
        issue_status: Issue status
        issue_url: Issue URL
        issue_source: Source system identifier (e.g., "tracker", "github", "linear")
    """
    if not issue_key:
        return
    props: dict[str, Any] = {"source": issue_source}
    if issue_status:
        props["status"] = issue_status
    if issue_url:
        props["url"] = issue_url
    upsert_node_fn(issue_key, labels=["ExternalIssue"], properties=props)
    upsert_edge_fn(source_id, "LINKED_ISSUE", issue_key)


def get_linked_external_issues(
    query_fn: Callable, node_id: str, issue_source: Optional[str] = None
) -> list[dict[str, Any]]:
    """
    Retrieve external issues linked to a specific node.

    Args:
        query_fn: Query execution function
        node_id: Node identifier to find linked issues for
        issue_source: Optional filter by source system (e.g., "tracker", "github")

    Returns:
        List of dicts with keys: key, status, url, summary, source
    """
    # Schema: (Node)-[:LINKED_ISSUE]->(ExternalIssue)

    if issue_source:
        cypher = (
            "MATCH (i:ExternalIssue {tenant_id: $tenant_id})<-[:LINKED_ISSUE]-(n {id: $node_id, tenant_id: $tenant_id}) "
            "WHERE i.source = $source "
            "RETURN i.id, i.status, i.url, i.source"
        )
        results = query_fn(cypher, {"node_id": node_id, "source": issue_source})
    else:
        cypher = (
            "MATCH (i:ExternalIssue {tenant_id: $tenant_id})<-[:LINKED_ISSUE]-(n {id: $node_id, tenant_id: $tenant_id}) "
            "RETURN i.id, i.status, i.url, i.source"
        )
        results = query_fn(cypher, {"node_id": node_id})

    issues = []
    if results and isinstance(results, list):
        for row in results:
            if isinstance(row, list) and len(row) >= 4:
                if row[0] == "key":
                    continue

                key = row[0]
                if isinstance(key, list):
                    key = key[0] if key else None

                status = row[1]
                if isinstance(status, list):
                    status = status[0] if status else None

                url = row[2]
                if isinstance(url, list):
                    url = url[0] if url else None

                source = row[3]
                if isinstance(source, list):
                    source = source[0] if source else "external"

                if key:
                    issues.append(
                        {
                            "key": str(key),
                            "status": str(status) if status else None,
                            "url": str(url) if url else None,
                            "source": str(source) if source else "external",
                        }
                    )

    return issues


def record_document_feedback(
    query_fn: Callable,
    document_id: str,
    feedback: str,
    comment: Optional[str] = None,
) -> None:
    """
    Update feedback counters on a Document node.

    Tracks:
    - feedback_total
    - feedback_positive / feedback_negative
    - last_feedback_at (ISO8601)
    - last_feedback_comment (truncated to 500 chars)

    Args:
        query_fn: Query execution function
        document_id: Document identifier
        feedback: Feedback sentiment ("positive" or "negative")
        comment: Optional feedback comment
    """
    if not document_id:
        return

    sentiment = feedback.strip().lower()
    is_positive = sentiment == "positive"
    is_negative = sentiment == "negative"
    now_iso = datetime.now(timezone.utc).isoformat()
    trimmed_comment = (comment or "").strip()
    if trimmed_comment:
        trimmed_comment = trimmed_comment[:500]  # security/readability limit

    cypher = (
        "MERGE (d:Document {id: $id, tenant_id: $tenant_id}) "
        "SET d.feedback_total = coalesce(d.feedback_total, 0) + 1, "
        "    d.last_feedback_at = $now "
        + (
            "    , d.feedback_positive = coalesce(d.feedback_positive, 0) + 1 "
            if is_positive
            else ""
        )
        + (
            "    , d.feedback_negative = coalesce(d.feedback_negative, 0) + 1 "
            if is_negative
            else ""
        )
        + ("    , d.last_feedback_comment = $comment " if trimmed_comment else "")
        + "RETURN d.id"
    )
    query_fn(
        cypher,
        {"id": document_id, "now": now_iso, "comment": trimmed_comment},
    )

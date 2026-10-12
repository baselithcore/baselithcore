"""SQL for :mod:`baselith.core.orchestration.checkpoint_postgres`.

Split out for the module size cap. The schema DDL stays in
``checkpoint_postgres`` itself: ``tests/unit/test_schema_ownership.py`` pins
which modules may create Postgres tables.

Every write here is a **compare-and-swap** on the ``version`` column: the
statement matches only while the stored version still equals the writer's, so
a writer holding a stale copy changes nothing and the store raises
``CheckpointConflictError``. It is also **fenced on the lease owner**: a write
matches only a row that is unowned or owned by the writer. A replica that
lost its lease and reloads the row (so its version is current again) still
cannot write over the new owner's run, or take it back; taking a run over
goes through the claim, which checks the lease and bumps the version. ``version``, ``owner`` and ``lease_expires_at``
columns are authoritative over the copies inside ``data``: the lease
statements update only the columns.

**Lease times are on the database clock.** ``lease_expires_at`` is written as
``now()`` on the server plus a *duration* and read back as the duration still
left, never as an absolute time taken from a replica. Every replica therefore
measures every lease against the same clock, and a replica whose own clock is
off by more than a lease length can neither steal a live run (its "now" would
say the lease had expired) nor keep a dead one locked. In-process values stay
on the local clock: the store converts at the boundary (see
``checkpoint_postgres``), and durations are clock-independent.
"""

from __future__ import annotations

from typing import Any

# The database clock below is spelled out in every statement, as
# ``EXTRACT(EPOCH FROM now())::float8`` (Unix seconds; ``EXTRACT`` returns
# ``numeric`` on PostgreSQL 14+ and the cast keeps the arithmetic in the
# column's ``double precision``), rather than spliced in: the statements stay
# literals, which is what the SQL-injection lint can verify. Reads return
# ``lease_remaining`` — the seconds left on the lease by that clock, negative
# once expired, NULL when unowned — in place of the absolute expiry.

_HISTORY_INSERT = """
INSERT INTO agent_checkpoint_history (run_id, version, status, step, data)
VALUES (%s, %s, %s, %s, %s)
ON CONFLICT (run_id, version) DO NOTHING
"""

# Snapshot for the save_step fast-path: copy the just-patched live row
# server-side, so no full-data payload crosses the wire and the O(n) write
# property of save_step is preserved.
_HISTORY_SNAPSHOT_FROM_LIVE = """
INSERT INTO agent_checkpoint_history (run_id, version, status, step, data)
SELECT run_id, version, status, COALESCE((data->>'step')::int, 0), data
FROM agent_checkpoints WHERE run_id = %s
ON CONFLICT (run_id, version) DO NOTHING
"""

# Keep only the newest N snapshots per run.
_HISTORY_TRIM = """
DELETE FROM agent_checkpoint_history
WHERE run_id = %s AND version < (
    SELECT COALESCE(MIN(version), 0) FROM (
        SELECT version FROM agent_checkpoint_history
        WHERE run_id = %s ORDER BY version DESC LIMIT %s
    ) AS newest
)
"""

_HISTORY_LIST = """
SELECT version, status, step,
       COALESCE((data->>'updated_at')::float8, EXTRACT(EPOCH FROM created_at))
           AS updated_at
FROM agent_checkpoint_history WHERE run_id = %s ORDER BY version ASC
"""

_HISTORY_LOAD = """
SELECT data FROM agent_checkpoint_history WHERE run_id = %s AND version = %s
"""

# Insert a new run, or overwrite it while the stored version is the expected
# one. ``DO UPDATE ... WHERE`` leaves a conflicting row untouched (rowcount 0).
# The lease parameter is the seconds LEFT on it (NULL keeps it NULL).
_UPSERT = """
INSERT INTO agent_checkpoints
    (run_id, tenant_id, status, data, version, owner, lease_expires_at, updated_at)
VALUES (%s, %s, %s, %s, %s, %s,
        EXTRACT(EPOCH FROM now())::float8 + %s::float8, NOW())
ON CONFLICT (run_id) DO UPDATE SET
    tenant_id = EXCLUDED.tenant_id,
    status = EXCLUDED.status,
    data = EXCLUDED.data,
    version = EXCLUDED.version,
    owner = EXCLUDED.owner,
    lease_expires_at = EXCLUDED.lease_expires_at,
    updated_at = NOW()
WHERE agent_checkpoints.version = %s
  AND (agent_checkpoints.owner IS NULL OR agent_checkpoints.owner = %s)
"""

# Incremental step write: the new step entry and trajectory element are the
# only per-step payload; every other top-level field (budget, plugin_data,
# inputs, status, …) is merged in with ``||`` so a later load() sees exactly
# what a full save() would have produced. The jsonb_set calls apply to the
# merged value; the trajectory append reads the OLD column value, which ``||``
# never touches (the head carries no trajectory).
_STEP_UPDATE = """
UPDATE agent_checkpoints SET
    data = jsonb_set(
        jsonb_set(
            data || %s::jsonb,
            ARRAY['steps', %s], %s::jsonb, true
        ),
        '{trajectory}',
        COALESCE(data->'trajectory', '[]'::jsonb) || %s::jsonb,
        true
    ),
    status = %s,
    version = %s,
    owner = %s,
    lease_expires_at = EXTRACT(EPOCH FROM now())::float8 + %s::float8,
    updated_at = NOW()
WHERE run_id = %s AND version = %s AND (owner IS NULL OR owner = %s)
"""

_LOAD = """
SELECT data, version, owner,
       lease_expires_at - EXTRACT(EPOCH FROM now())::float8 AS lease_remaining
FROM agent_checkpoints WHERE run_id = %s
"""

# Take the lease when the run is unowned, expired, or already ours. Taking
# over from another owner bumps the version (fencing); in a SET list the
# right-hand ``owner`` is the row's old value. Both the new expiry and the
# "has it expired" test read the database clock; the two float parameters are
# the lease length and a clock shift that is 0 outside tests (``now=``).
_CLAIM = """
UPDATE agent_checkpoints SET
    version = CASE WHEN owner IS DISTINCT FROM %s THEN version + 1 ELSE version END,
    owner = %s,
    lease_expires_at = EXTRACT(EPOCH FROM now())::float8 + %s::float8
WHERE run_id = %s
  AND (owner IS NULL OR owner = %s
       OR lease_expires_at IS NULL
       OR lease_expires_at <= EXTRACT(EPOCH FROM now())::float8 + %s::float8)
RETURNING data, version, owner,
          lease_expires_at - EXTRACT(EPOCH FROM now())::float8 AS lease_remaining
"""

_RENEW = """
UPDATE agent_checkpoints
SET lease_expires_at = EXTRACT(EPOCH FROM now())::float8 + %s::float8
WHERE run_id = %s AND owner = %s
"""

_RELEASE = """
UPDATE agent_checkpoints SET owner = NULL, lease_expires_at = NULL
WHERE run_id = %s AND owner = %s
"""

# Run listing for operator/read surfaces: any status, newest first. The live
# row is the source (history rows are per-version copies of the same run).
#
# Two costs are designed out here. The heavy keys ``summarize_run`` discards
# anyway (``steps``, ``trajectory``, ``plugin_data``, ``answer``) are stripped
# server-side, so a 500-run page no longer ships every tool output of every run
# to be parsed and thrown away (seeded runs: 427 kB -> 94 kB per page); only
# the trajectory's length crosses the wire. And the filters are appended only
# when given, instead of the ``(%s IS NULL OR col = %s)`` catch-all: once
# psycopg prepares the statement (5th execution) Postgres may switch to a
# generic plan, and a generic plan cannot use an index for a predicate that
# might be ``NULL`` — EXPLAIN showed a seq scan over every tenant's runs.
_RUN_LIST_SELECT = (
    "SELECT data - 'steps' - 'trajectory' - 'plugin_data' - 'answer' "
    "- 'inputs' AS data, "
    "CASE WHEN jsonb_typeof(data->'trajectory') = 'array' "
    "THEN jsonb_array_length(data->'trajectory') ELSE 0 END AS trajectory_length "
    "FROM agent_checkpoints"
)


def _run_list_query(
    tenant_id: str | None, status: str | None, limit: int
) -> tuple[str, list[Any]]:
    """Build the run-listing statement with only the filters actually given."""
    where: list[str] = []
    params: list[Any] = []
    if tenant_id is not None:
        where.append("tenant_id = %s")
        params.append(tenant_id)
    if status is not None:
        where.append("status = %s")
        params.append(status)
    sql = _RUN_LIST_SELECT
    if where:
        sql += " WHERE " + " AND ".join(where)
    params.append(limit)
    return sql + " ORDER BY updated_at DESC LIMIT %s", params

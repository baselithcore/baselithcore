#!/usr/bin/env python3
"""Disaster-recovery drill: prove a Postgres backup restores, row for row.

Run by the nightly ``restore_drill`` job (.github/workflows/nightly.yml); each
subcommand is one step of it, so a red run names the phase that broke.

``seed``
    Write a known set of rows into a migrated database: unicode, NULLs, JSONB,
    a foreign key, a ``BIGSERIAL`` whose sequence position has to survive.
``backup``
    Take the dump exactly the way the Helm backup CronJob does. The chart is
    rendered with ``helm template`` and the ``pg-backup`` container's own
    script runs in the chart's own image, so a change to that script or image
    is what the drill exercises the next night — nothing here re-types it.
``restore``
    Create a fresh database and restore the dump into it with
    ``scripts/restore-db.sh --direct``, run in the same image that wrote the
    dump (a psql older than the pg_dump that wrote a file can refuse it).
``compare``
    Fingerprint both databases — every table's row count and content checksum,
    columns, constraints, indexes, row-level-security flags and policies,
    sequence positions, extensions — and fail on any difference.

Connection settings come from the libpq environment (``PGHOST``, ``PGPORT``,
``PGUSER``, ``PGPASSWORD``); database names are arguments. Docker reaches the
database through ``--docker-network`` (``host`` on a CI runner).
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

import psycopg
import yaml
from psycopg import sql

ROOT = Path(__file__).resolve().parent.parent
CHART = ROOT / "deploy" / "helm" / "baselithcore"
RESTORE_SCRIPT = ROOT / "scripts" / "restore-db.sh"

#: The libpq variables the chart's dump container is given (backupEnv in
#: _helpers.tpl). If the chart grows one, the drill must learn about it.
_DUMP_ENV = (
    "BACKUP_DIR",
    "RETENTION_DAYS",
    "PGHOST",
    "PGPORT",
    "PGDATABASE",
    "PGUSER",
    "PGPASSWORD",
)

#: Tables the seed writes and the comparison insists are non-empty afterwards.
SEEDED_TABLES = (
    "tenants",
    "chat_feedback",
    "interactions",
    "feedback",
    "webhook_endpoints",
    "webhook_deliveries",
)


def _connect(dbname: str, *, autocommit: bool = False) -> psycopg.Connection[Any]:
    return psycopg.connect(dbname=dbname, autocommit=autocommit)


# --------------------------------------------------------------------------
# seed
# --------------------------------------------------------------------------
def seed(dbname: str) -> None:
    with _connect(dbname) as conn, conn.cursor() as cur:
        cur.execute(
            "INSERT INTO tenants (id, name, status) VALUES "
            "('default', 'Default', 'active'), ('acme', 'Acme Ünïcode ✓', 'suspended')"
        )
        cur.executemany(
            "INSERT INTO chat_feedback (query, answer, feedback, conversation_id, sources, comment, tenant_id) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s)",
            [
                ("q1", "a1", "positive", "c-1", None, None, "default"),
                (
                    "q2 — ‘quoted’",
                    "a2\nmultiline\ttab",
                    "negative",
                    "c-1",
                    "[]",
                    "why",
                    "acme",
                ),
                ("q3", "a3", "positive", None, None, "", "acme"),
            ],
        )
        # Burn sequence values: the restored sequence must resume past them.
        cur.execute("SELECT nextval('chat_feedback_id_seq') FROM generate_series(1, 5)")
        cur.execute(
            "INSERT INTO interactions (id, session_id, user_id, agent_id, metadata) VALUES "
            "('00000000-0000-4000-8000-000000000001', 's1', 'u1', 'agent', '{\"k\": [1, 2, {\"n\": null}]}'),"
            "('00000000-0000-4000-8000-000000000002', 's2', NULL, NULL, '{}')"
        )
        cur.execute(
            "INSERT INTO feedback (id, interaction_id, score, label, metadata) VALUES "
            "('00000000-0000-4000-8000-0000000000f1', '00000000-0000-4000-8000-000000000001', 0.875, 'good', '{}'),"
            "('00000000-0000-4000-8000-0000000000f2', '00000000-0000-4000-8000-000000000002', -1e-9, NULL, '{\"x\": 1}')"
        )
        cur.execute(
            "INSERT INTO webhook_endpoints (id, tenant_id, url, secret, created_at) VALUES "
            "('wh-1', 'acme', 'https://example.invalid/hook', 'drill-not-a-secret', 1760000000.125)"
        )
        cur.execute(
            "INSERT INTO webhook_deliveries (id, endpoint_id, event_id, event_type, tenant_id, url, "
            "status, attempts, created_at, payload) VALUES "
            "('d-1', 'wh-1', 'e-1', 'chat.completed', 'acme', 'https://example.invalid/hook', "
            "'failed', 3, 1760000001.5, '{\"body\": \"ok\"}')"
        )
    print(f"seeded {dbname}: {', '.join(SEEDED_TABLES)}")


# --------------------------------------------------------------------------
# backup
# --------------------------------------------------------------------------
def _rendered_backup_container() -> tuple[str, str, set[str]]:
    """(image, script, env names) of the chart's ``pg-backup`` container."""
    rendered = subprocess.run(
        [
            "helm",
            "template",
            "drill",
            str(CHART),
            "--show-only",
            "templates/backup-cronjob.yaml",
            "--set",
            "backup.enabled=true",
        ],
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    cronjob = yaml.safe_load(rendered)
    pod = cronjob["spec"]["jobTemplate"]["spec"]["template"]["spec"]
    containers = pod.get("initContainers", []) + pod["containers"]
    matches = [c for c in containers if c["name"] == "pg-backup"]
    if len(matches) != 1:
        raise SystemExit(
            f"expected one pg-backup container in the chart, found {len(matches)}"
        )
    container = matches[0]
    command = container["command"]
    if command[:2] != ["/bin/sh", "-c"] or len(command) != 3:
        raise SystemExit(
            f"pg-backup command changed shape: {command[:2]!r}; update the drill"
        )
    return container["image"], command[2], {e["name"] for e in container["env"]}


def _docker_env_args(names: dict[str, str]) -> list[str]:
    args: list[str] = []
    for name in names:
        args += ["-e", name]  # value taken from the subprocess environment
    return args


def backup(
    dbname: str, out_dir: Path, network: str, docker_pg: tuple[str, str]
) -> Path:
    image, script, env_names = _rendered_backup_container()
    if env_names != set(_DUMP_ENV):
        raise SystemExit(
            f"the chart's pg-backup env changed: {sorted(env_names ^ set(_DUMP_ENV))}; update the drill"
        )
    out_dir.mkdir(parents=True, exist_ok=True)
    env = {
        "BACKUP_DIR": "/backups",
        "RETENTION_DAYS": "30",
        "PGHOST": docker_pg[0],
        "PGPORT": docker_pg[1],
        "PGDATABASE": dbname,
        "PGUSER": os.environ["PGUSER"],
        "PGPASSWORD": os.environ["PGPASSWORD"],
    }
    print(
        f"backup: chart image {image}; running the chart's pg-backup script against {dbname}"
    )
    started = time.monotonic()
    subprocess.run(
        [
            "docker",
            "run",
            "--rm",
            "--network",
            network,
            "--user",
            f"{os.getuid()}:{os.getgid()}",
            "-v",
            f"{out_dir.resolve()}:/backups",
            *_docker_env_args(env),
            image,
            "/bin/sh",
            "-c",
            script,
        ],
        check=True,
        env={**os.environ, **env},
    )
    dumps = sorted(out_dir.glob("backup_*.sql.gz"))
    partials = sorted(out_dir.glob(".backup_*.partial"))
    if len(dumps) != 1 or partials:
        raise SystemExit(
            f"expected exactly one dump and no partials, got {dumps} / {partials}"
        )
    print(
        f"backup: {dumps[0].name} ({dumps[0].stat().st_size} bytes) in {time.monotonic() - started:.1f}s"
    )
    return dumps[0]


# --------------------------------------------------------------------------
# restore
# --------------------------------------------------------------------------
def restore(
    backup_file: Path, dbname: str, network: str, docker_pg: tuple[str, str]
) -> None:
    image, _script, _env = _rendered_backup_container()
    with _connect("postgres", autocommit=True) as conn:
        # CREATE, never DROP: the drill restores into a database it made, and
        # refuses to run if the name is taken rather than delete anything.
        conn.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(dbname)))
    env = {
        "PGHOST": docker_pg[0],
        "PGPORT": docker_pg[1],
        "PGDATABASE": dbname,
        "PGUSER": os.environ["PGUSER"],
        "PGPASSWORD": os.environ["PGPASSWORD"],
    }
    backup_file = backup_file.resolve()
    print(
        f"restore: scripts/restore-db.sh {backup_file.name} --direct -> {dbname} (in {image})"
    )
    started = time.monotonic()
    subprocess.run(
        [
            "docker",
            "run",
            "--rm",
            "--network",
            network,
            "--user",
            f"{os.getuid()}:{os.getgid()}",
            "-v",
            f"{RESTORE_SCRIPT}:/drill/restore-db.sh:ro",
            "-v",
            f"{backup_file.parent}:/backups:ro",
            *_docker_env_args(env),
            image,
            "bash",
            "/drill/restore-db.sh",
            f"/backups/{backup_file.name}",
            "--direct",
        ],
        check=True,
        env={**os.environ, **env},
    )
    print(
        f"restore: done in {time.monotonic() - started:.1f}s (the drill's RTO for this dump)"
    )


# --------------------------------------------------------------------------
# compare
# --------------------------------------------------------------------------
_CATALOG_QUERIES = {
    "tables": """
        SELECT c.relname, c.relrowsecurity, c.relforcerowsecurity
        FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
        WHERE n.nspname = 'public' AND c.relkind IN ('r', 'p') ORDER BY 1""",
    "columns": """
        SELECT table_name, column_name, data_type, column_default, is_nullable
        FROM information_schema.columns WHERE table_schema = 'public' ORDER BY 1, 2""",
    "constraints": """
        SELECT conrelid::regclass::text, conname, pg_get_constraintdef(oid)
        FROM pg_constraint WHERE connamespace = 'public'::regnamespace ORDER BY 1, 2""",
    "indexes": """
        SELECT tablename, indexname, indexdef FROM pg_indexes
        WHERE schemaname = 'public' ORDER BY 1, 2""",
    "policies": """
        SELECT tablename, policyname, permissive, roles::text, cmd, qual, with_check
        FROM pg_policies WHERE schemaname = 'public' ORDER BY 1, 2""",
    "sequences": """
        SELECT sequencename, last_value FROM pg_sequences
        WHERE schemaname = 'public' ORDER BY 1""",
    "extensions": "SELECT extname, extversion FROM pg_extension ORDER BY 1",
}


def fingerprint(dbname: str) -> dict[str, Any]:
    result: dict[str, Any] = {}
    with _connect(dbname) as conn, conn.cursor() as cur:
        for name, query in _CATALOG_QUERIES.items():
            cur.execute(query)
            result[name] = [list(row) for row in cur.fetchall()]
        data: dict[str, list[Any]] = {}
        for table, *_flags in result["tables"]:
            cur.execute(
                sql.SQL(
                    "SELECT count(*), md5(coalesce(string_agg(t::text, E'\\n' ORDER BY t::text), '')) "
                    "FROM public.{} AS t"
                ).format(sql.Identifier(table))
            )
            row = cur.fetchone()
            assert row is not None
            data[table] = [row[0], row[1]]
        result["data"] = data
    return result


def compare(source: str, target: str) -> int:
    expected, actual = fingerprint(source), fingerprint(target)
    failures = [
        f"{section}: {source}={expected[section]!r}\n{' ' * len(section)}  {target}={actual[section]!r}"
        for section in expected
        if section != "data" and expected[section] != actual[section]
    ]
    for table in sorted(set(expected["data"]) | set(actual["data"])):
        if expected["data"].get(table) != actual["data"].get(table):
            failures.append(
                f"data {table}: {source}={expected['data'].get(table)} {target}={actual['data'].get(table)}"
            )
    empty = [t for t in SEEDED_TABLES if actual["data"].get(t, [0])[0] == 0]
    if empty:
        failures.append(f"seeded tables restored empty: {empty}")
    print(f"{'table':<28} {'rows':>6}  checksum")
    for table, (rows, digest) in sorted(actual["data"].items()):
        mark = "ok" if expected["data"].get(table) == [rows, digest] else "DIFF"
        print(f"{table:<28} {rows:>6}  {digest}  {mark}")
    print(json.dumps({k: len(v) for k, v in actual.items()}, sort_keys=True))
    if failures:
        print(
            "\nRESTORE DRILL FAILED — the restored database differs from the source:",
            file=sys.stderr,
        )
        for failure in failures:
            print(f"  - {failure}", file=sys.stderr)
        return 1
    print(f"\nrestore drill passed: {target} is identical to {source}")
    return 0


# --------------------------------------------------------------------------
# cli
# --------------------------------------------------------------------------
def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_seed = sub.add_parser(
        "seed", help="write the known rows into a migrated database"
    )
    p_seed.add_argument("--db", required=True)

    for name, help_text in (
        ("backup", "dump the way the Helm CronJob does"),
        ("restore", "restore-db.sh into a fresh db"),
    ):
        p = sub.add_parser(name, help=help_text)
        p.add_argument("--db", required=True)
        p.add_argument("--docker-network", default="host")
        p.add_argument(
            "--docker-pghost",
            default=None,
            help="database host as seen from the container (default: $PGHOST)",
        )
        p.add_argument(
            "--docker-pgport",
            default=None,
            help="database port as seen from the container (default: $PGPORT)",
        )
        if name == "backup":
            p.add_argument("--out-dir", type=Path, required=True)
        else:
            p.add_argument("--backup-file", type=Path, required=True)

    p_cmp = sub.add_parser(
        "compare", help="fingerprint two databases and fail on any difference"
    )
    p_cmp.add_argument("--source", required=True)
    p_cmp.add_argument("--target", required=True)

    args = parser.parse_args(argv)
    # Line-buffered, so these lines interleave in order with the docker output.
    sys.stdout.reconfigure(line_buffering=True)  # type: ignore[union-attr]
    if args.command == "seed":
        seed(args.db)
        return 0
    if args.command == "compare":
        return compare(args.source, args.target)
    docker_pg = (
        args.docker_pghost or os.environ.get("PGHOST", "127.0.0.1"),
        args.docker_pgport or os.environ.get("PGPORT", "5432"),
    )
    if args.command == "backup":
        dump = backup(args.db, args.out_dir, args.docker_network, docker_pg)
        github_output = os.environ.get("GITHUB_OUTPUT")
        if github_output:
            with open(github_output, "a", encoding="utf-8") as fh:
                fh.write(f"file={dump}\n")
        return 0
    restore(args.backup_file, args.db, args.docker_network, docker_pg)
    return 0


if __name__ == "__main__":
    sys.exit(main())

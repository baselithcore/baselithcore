"""The backup CronJob snapshots the release's own Qdrant beside the dump."""

from __future__ import annotations

from tests.unit.helm import documents, render

BACKUP = ("--set", "backup.enabled=true")
QDRANT = ("--set", "qdrant.enabled=true")
OFFSITE = (
    "--set",
    "backup.offsite.enabled=true",
    "--set",
    "backup.offsite.path=bucket/cell",
    "--set",
    "backup.offsite.remote.type=s3",
)


def _pod(*args: str) -> dict:
    cron = next(d for d in documents(render(*args)) if d["kind"] == "CronJob")
    return cron["spec"]["jobTemplate"]["spec"]["template"]["spec"]


def _names(items: list[dict]) -> list[str]:
    return [c["name"] for c in items]


def test_no_qdrant_keeps_the_dump_only_layout() -> None:
    pod = _pod(*BACKUP)
    assert _names(pod["initContainers"]) == ["wait-for-db"]
    assert _names(pod["containers"]) == ["pg-backup"]


def test_snapshot_runs_after_the_dump_never_beside_it() -> None:
    pod = _pod(*BACKUP, *QDRANT)
    assert _names(pod["initContainers"]) == ["wait-for-db", "pg-backup"]
    assert _names(pod["containers"]) == ["qdrant-snapshot"]


def test_offsite_uploads_both_after_both_finished() -> None:
    pod = _pod(*BACKUP, *QDRANT, *OFFSITE)
    assert _names(pod["initContainers"]) == [
        "wait-for-db",
        "pg-backup",
        "qdrant-snapshot",
    ]
    (upload,) = pod["containers"]
    script = upload["command"][2]
    # copy, prune and list all cover both kinds of file.
    assert script.count("--include 'backup_*.sql.gz' --include 'qdrant_*.tar.gz'") == 3


def test_snapshot_targets_the_release_qdrant_with_its_key() -> None:
    pod = _pod(*BACKUP, *QDRANT)
    (snap,) = pod["containers"]
    env = {e["name"]: e for e in snap["env"]}
    assert env["QDRANT_URL"]["value"] == "http://release-baselithcore-qdrant:6333"
    ref = env["QDRANT_API_KEY"]["valueFrom"]["secretKeyRef"]
    assert ref["key"] == "BASELITH_QDRANT_API_KEY"
    assert ref["optional"] is True
    # The key travels in a header file, never on curl's command line.
    script = snap["command"][2]
    assert "api-key: %s" in script
    assert '-H "@${WORK}/headers"' in script
    assert snap["volumeMounts"] == [{"name": "backups", "mountPath": "/backups"}]
    assert snap["resources"]["limits"]["memory"]


def test_a_refused_listing_fails_the_run() -> None:
    script = _pod(*BACKUP, *QDRANT)["containers"][0]["command"][2]
    listing = script[script.index('"${QDRANT_URL}/collections"') - 40 :][:200]
    assert '-o "${WORK}/collections.json"' in listing
    assert "|| true" not in listing.split("\n")[0]


def test_snapshot_can_be_switched_off() -> None:
    pod = _pod(*BACKUP, *QDRANT, "--set", "backup.qdrant.enabled=false")
    assert _names(pod["containers"]) == ["pg-backup"]


def test_backup_qdrant_without_the_server_renders_nothing_extra() -> None:
    pod = _pod(*BACKUP, *OFFSITE)
    assert "qdrant-snapshot" not in _names(pod["initContainers"])

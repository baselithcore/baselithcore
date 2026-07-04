"""
Load-campaign runner: Locust + SLO comparison (V&V criteria V1–V3).

Wraps the existing ``tests/load/locustfile.py`` profile in a campaign with
fixed shapes, captures Locust's CSV stats, compares the aggregate against the
declared SLOs (``deploy/prometheus/slo-rules.yml``: availability 99.9%,
p99 < 1s), and writes the evidence-pack report::

    python tests/load/campaign.py --profile baseline \\
        --host http://localhost:8000 --out validation-reports/2026-07-04-load

Profiles: ``smoke`` (10u/2m pre-flight), ``baseline`` (50u/10m — the V1/V2
acceptance run), ``stress`` (200u/15m, SLO comparison informative only),
``soak`` (40u/6h — V3 endurance; override with ``--duration``).

Exit codes: 0 = SLOs met, 1 = SLO breach, 2 = execution error.
Not collected by pytest (no ``test_`` prefix); requires ``pip install -e
".[load]"`` and a running target.
"""

from __future__ import annotations

import argparse
import csv
import json
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

LOCUSTFILE = Path(__file__).parent / "locustfile.py"

# SLO targets mirrored from deploy/prometheus/slo-rules.yml.
DEFAULT_SLO_AVAILABILITY = 0.999
DEFAULT_SLO_P99_MS = 1000.0


@dataclass(frozen=True)
class Profile:
    users: int
    spawn_rate: int
    duration: str
    slo_blocking: bool  # stress runs report SLOs but never fail on them


PROFILES: dict[str, Profile] = {
    "smoke": Profile(users=10, spawn_rate=5, duration="2m", slo_blocking=True),
    "baseline": Profile(users=50, spawn_rate=10, duration="10m", slo_blocking=True),
    "stress": Profile(users=200, spawn_rate=20, duration="15m", slo_blocking=False),
    "soak": Profile(users=40, spawn_rate=5, duration="6h", slo_blocking=True),
}


@dataclass(frozen=True)
class AggregateStats:
    requests: int
    failures: int
    p50_ms: float
    p95_ms: float
    p99_ms: float
    rps: float

    @property
    def availability(self) -> float:
        if self.requests == 0:
            return 0.0
        return 1.0 - (self.failures / self.requests)


def run_locust(*, host: str, profile: Profile, duration: str, csv_prefix: Path) -> int:
    """Run Locust headless; returns its exit code (non-zero tolerated —
    Locust exits 1 when any request failed, which the SLO judges instead)."""
    cmd = [
        sys.executable,
        "-m",
        "locust",
        "-f",
        str(LOCUSTFILE),
        "--host",
        host,
        "--headless",
        "-u",
        str(profile.users),
        "-r",
        str(profile.spawn_rate),
        "-t",
        duration,
        "--csv",
        str(csv_prefix),
        "--csv-full-history",
        "--only-summary",
    ]
    print(f"$ {' '.join(cmd)}", flush=True)
    return subprocess.run(cmd, check=False).returncode


def parse_aggregate(stats_csv: Path) -> AggregateStats:
    """Extract the 'Aggregated' row from a Locust ``*_stats.csv``."""
    with stats_csv.open(newline="", encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            if row.get("Name") != "Aggregated":
                continue
            return AggregateStats(
                requests=int(float(row["Request Count"])),
                failures=int(float(row["Failure Count"])),
                p50_ms=float(row["50%"]),
                p95_ms=float(row["95%"]),
                p99_ms=float(row["99%"]),
                rps=float(row["Requests/s"]),
            )
    raise ValueError(f"no 'Aggregated' row in {stats_csv}")


def to_markdown(
    *,
    profile_name: str,
    profile: Profile,
    duration: str,
    host: str,
    stats: AggregateStats,
    slo_availability: float,
    slo_p99_ms: float,
    passed: bool,
) -> str:
    avail_ok = stats.availability >= slo_availability
    p99_ok = stats.p99_ms <= slo_p99_ms
    blocking = "blocking" if profile.slo_blocking else "informative (stress)"
    return "\n".join(
        [
            f"# Load campaign report — {profile_name}",
            "",
            f"- **Target:** {host}",
            f"- **Profile:** {profile_name} — {profile.users} users, "
            f"spawn {profile.spawn_rate}/s, duration {duration}",
            f"- **SLO comparison:** {blocking}",
            f"- **Criteria exercised:** "
            f"{'V3 (+V1, V2 across window)' if profile_name == 'soak' else 'V1, V2'}",
            "",
            "## Results",
            "",
            "| Metric | Target | Measured | Pass |",
            "| ------ | ------ | -------- | ---- |",
            f"| Availability (V1) | ≥ {slo_availability:.3%} "
            f"| {stats.availability:.3%} | {'✅' if avail_ok else '❌'} |",
            f"| p99 latency (V2) | ≤ {slo_p99_ms:.0f} ms "
            f"| {stats.p99_ms:.0f} ms | {'✅' if p99_ok else '❌'} |",
            "",
            f"- Requests: {stats.requests} ({stats.rps:.1f} req/s), "
            f"failures: {stats.failures}",
            f"- Latency p50/p95/p99: {stats.p50_ms:.0f} / {stats.p95_ms:.0f} / "
            f"{stats.p99_ms:.0f} ms",
            "",
            "## Verdict",
            "",
            f"**{'PASS' if passed else 'FAIL'}**"
            + ("" if profile.slo_blocking else " (stress profile — never blocking)"),
            "",
        ]
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Run a load campaign and judge it against the SLOs."
    )
    parser.add_argument("--profile", choices=sorted(PROFILES), required=True)
    parser.add_argument("--host", required=True, help="base URL of the target")
    parser.add_argument(
        "--duration", default=None, help="override profile duration (e.g. 24h)"
    )
    parser.add_argument(
        "--out", type=Path, required=True, help="report directory (evidence pack)"
    )
    parser.add_argument(
        "--slo-availability", type=float, default=DEFAULT_SLO_AVAILABILITY
    )
    parser.add_argument("--slo-p99-ms", type=float, default=DEFAULT_SLO_P99_MS)
    args = parser.parse_args(argv)

    if shutil.which("locust") is None:
        print(
            "error: locust not installed — pip install -e '.[load]'",
            file=sys.stderr,
        )
        return 2

    profile = PROFILES[args.profile]
    duration = args.duration or profile.duration
    raw_dir = args.out / "raw"
    raw_dir.mkdir(parents=True, exist_ok=True)
    csv_prefix = raw_dir / "locust"

    run_locust(
        host=args.host, profile=profile, duration=duration, csv_prefix=csv_prefix
    )

    stats_csv = Path(f"{csv_prefix}_stats.csv")
    if not stats_csv.exists():
        print(f"error: {stats_csv} not produced — locust run failed", file=sys.stderr)
        return 2
    try:
        stats = parse_aggregate(stats_csv)
    except (ValueError, KeyError) as exc:
        print(f"error parsing locust stats: {exc}", file=sys.stderr)
        return 2
    if stats.requests == 0:
        print("error: zero requests recorded — target unreachable?", file=sys.stderr)
        return 2

    slo_met = (
        stats.availability >= args.slo_availability and stats.p99_ms <= args.slo_p99_ms
    )
    passed = slo_met or not profile.slo_blocking

    report_md = to_markdown(
        profile_name=args.profile,
        profile=profile,
        duration=duration,
        host=args.host,
        stats=stats,
        slo_availability=args.slo_availability,
        slo_p99_ms=args.slo_p99_ms,
        passed=slo_met,
    )
    (args.out / "report.md").write_text(report_md, encoding="utf-8")
    (args.out / "report.json").write_text(
        json.dumps(
            {
                "profile": args.profile,
                "host": args.host,
                "duration": duration,
                "requests": stats.requests,
                "failures": stats.failures,
                "availability": stats.availability,
                "p50_ms": stats.p50_ms,
                "p95_ms": stats.p95_ms,
                "p99_ms": stats.p99_ms,
                "rps": stats.rps,
                "slo": {
                    "availability": args.slo_availability,
                    "p99_ms": args.slo_p99_ms,
                    "met": slo_met,
                    "blocking": profile.slo_blocking,
                },
            },
            indent=2,
            sort_keys=True,
        ),
        encoding="utf-8",
    )
    print(report_md)
    print(f"report written to {args.out}/", flush=True)
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())

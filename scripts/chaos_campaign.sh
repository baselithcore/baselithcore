#!/usr/bin/env bash
# Chaos campaign against a Compose-deployed instance (V&V criterion V4).
#
# For each infrastructure dependency, stops the Docker Compose service,
# probes the API during the outage, restarts it, and measures time to
# recovery. Judged against V4 (see mkdocs-site/docs/validation/vv-plan.md):
#   - /health keeps answering during the outage
#   - recovery to healthy /status within 60s of the dependency returning
# PostgreSQL is report-only (opt-in via --include-postgres).
#
# Usage:
#   scripts/chaos_campaign.sh --out validation-reports/2026-07-04-chaos \
#     [--target http://localhost:8000] [--services "redis qdrant"] \
#     [--include-postgres] [--outage-seconds 30] [--compose-file docker-compose.yml]
set -euo pipefail

TARGET="http://localhost:8000"
OUT=""
SERVICES="redis qdrant"
INCLUDE_POSTGRES=false
OUTAGE_SECONDS=30
RECOVERY_TIMEOUT=120
COMPOSE_FILE="docker-compose.yml"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --target) TARGET="$2"; shift 2 ;;
    --out) OUT="$2"; shift 2 ;;
    --services) SERVICES="$2"; shift 2 ;;
    --include-postgres) INCLUDE_POSTGRES=true; shift ;;
    --outage-seconds) OUTAGE_SECONDS="$2"; shift 2 ;;
    --compose-file) COMPOSE_FILE="$2"; shift 2 ;;
    *) echo "unknown argument: $1" >&2; exit 2 ;;
  esac
done

[[ -n "$OUT" ]] || { echo "error: --out is required" >&2; exit 2; }
[[ -f "$COMPOSE_FILE" ]] || { echo "error: $COMPOSE_FILE not found (run from repo root)" >&2; exit 2; }
mkdir -p "$OUT/raw"
REPORT="$OUT/report.md"
$INCLUDE_POSTGRES && SERVICES="$SERVICES postgres"

probe() { # probe <path> -> 0 if 2xx
  curl -fsS -m 5 -o /dev/null "$TARGET$1" 2>/dev/null
}

compose() { docker compose -f "$COMPOSE_FILE" "$@"; }

echo "baseline check against $TARGET ..."
probe /health || { echo "error: target unhealthy before campaign — aborting" >&2; exit 2; }

{
  echo "# Chaos campaign report — $(date +%F)"
  echo
  echo "- **Target:** $TARGET"
  echo "- **Outage window:** ${OUTAGE_SECONDS}s per dependency"
  echo "- **Criteria exercised:** V4"
  echo
  echo "## Results"
  echo
  echo "| Dependency | /health during outage | Recovery (s) | V4 pass |"
  echo "| ---------- | --------------------- | ------------ | ------- |"
} > "$REPORT"

OVERALL_PASS=true

for SVC in $SERVICES; do
  echo "=== chaos: stopping '$SVC' for ${OUTAGE_SECONDS}s ==="
  LOG="$OUT/raw/$SVC.log"
  : > "$LOG"

  compose stop "$SVC" >> "$LOG" 2>&1

  HEALTH_OK=0; HEALTH_FAIL=0
  END=$(( $(date +%s) + OUTAGE_SECONDS ))
  while [[ $(date +%s) -lt $END ]]; do
    if probe /health; then HEALTH_OK=$((HEALTH_OK+1)); else HEALTH_FAIL=$((HEALTH_FAIL+1)); fi
    sleep 2
  done
  echo "outage probes: health ok=$HEALTH_OK fail=$HEALTH_FAIL" | tee -a "$LOG"

  compose start "$SVC" >> "$LOG" 2>&1
  RECOVERY_START=$(date +%s)
  RECOVERY_S="timeout"
  while [[ $(( $(date +%s) - RECOVERY_START )) -lt $RECOVERY_TIMEOUT ]]; do
    if probe /health && probe /status; then
      RECOVERY_S=$(( $(date +%s) - RECOVERY_START ))
      break
    fi
    sleep 2
  done
  echo "recovery: ${RECOVERY_S}s" | tee -a "$LOG"

  if [[ "$SVC" == "postgres" ]]; then
    PASS="report-only"
  elif [[ "$HEALTH_FAIL" -eq 0 && "$RECOVERY_S" != "timeout" && "$RECOVERY_S" -le 60 ]]; then
    PASS="✅"
  else
    PASS="❌"; OVERALL_PASS=false
  fi
  echo "| $SVC | ok=$HEALTH_OK fail=$HEALTH_FAIL | $RECOVERY_S | $PASS |" >> "$REPORT"
done

{
  echo
  echo "## Verdict"
  echo
  if $OVERALL_PASS; then echo "**PASS**"; else echo "**FAIL** — see raw/ logs"; fi
} >> "$REPORT"

echo
cat "$REPORT"
echo
echo "report written to $OUT/"
$OVERALL_PASS

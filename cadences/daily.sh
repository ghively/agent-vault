#!/bin/sh
# ============================================================================
# cadences/daily.sh — the cheap, idempotent daily cadence.
#
# Walks raw/ and turns new files into entity stubs. NO LLM call. NO network.
# Safe to run from cron every hour — the manifest makes it a no-op when nothing
# changed, and a single bad file is skipped (recorded) rather than aborting.
#
# Usage:  daily.sh [VAULT_DIR]   (default: parent dir of this script)
# ============================================================================
set -eu

. "$(cd "$(dirname "$0")" && pwd)/_common.sh"
resolve_vault "${1:-}"

start=$(date +%s)
rc=0
detail=""

# 1. Ingest new raw/ files into stubs. Idempotent via raw/_manifest.jsonl.
python3 ingest.py . || { rc=$?; detail="$detail ingest(rc=$rc)"; }

# 2. Validate. Cheap, and catches a corrupt write before it spreads.
python3 validate.py . >/dev/null || { rc=$?; detail="$detail validate(rc=$rc)"; }

record_run daily "$rc" "$(( $(date +%s) - start ))" "${detail:-ok}"
if [ "$rc" -eq 0 ]; then
    echo "daily: ok ($(date -u +%FT%TZ))"
else
    echo "daily: FAILED ($detail) — see discovery/_runs.jsonl" >&2
fi
exit "$rc"

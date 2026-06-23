#!/bin/sh
# ============================================================================
# cadences/weekly.sh â€” the expensive cadence (the LLM touchpoint).
#
# ingest -> compile -> promote -> validate. Each step runs even if an earlier
# one failed, so a single per-entity compile timeout (compiler.py exits 0 on
# partial success) never strands the discoveries that DID compile un-promoted.
# The LAST failing step's rc is the cadence rc (any nonzero step fails the
# run); a run record is always written.
#
# Model: AGENT_VAULT_COMPILER / OLLAMA_HOST / OLLAMA_MODEL. AGENT_VAULT_COMPILER=mock
# skips the LLM (CI / dry-runs).
#
# Usage:  weekly.sh [VAULT_DIR]
# ============================================================================
set -eu

. "$(cd "$(dirname "$0")" && pwd)/_common.sh"
resolve_vault "${1:-}"

start=$(date +%s)
rc=0
detail=""

# 1. Ingest anything new since the last daily.
python3 ingest.py .   || { rc=$?; detail="$detail ingest(rc=$rc)"; }

# 2. Compile (the LLM). Exits non-zero only on TOTAL failure, so partial
#    timeouts don't block promote/validate below.
python3 compiler.py . || { crc=$?; rc=$crc; detail="$detail compile(rc=$crc)"; }

# 3. Drain discoveries into the registry â€” runs regardless of compile outcome.
python3 promote.py .  || { prc=$?; rc=$prc; detail="$detail promote(rc=$prc)"; }

# 4. Validate the resulting tree.
python3 validate.py . >/dev/null || { vrc=$?; rc=$vrc; detail="$detail validate(rc=$vrc)"; }

record_run weekly "$rc" "$(( $(date +%s) - start ))" "${detail:-ok}"
if [ "$rc" -eq 0 ]; then
    echo "weekly: ok ($(date -u +%FT%TZ))"
else
    echo "weekly: completed with failures ($detail) â€” see discovery/_runs.jsonl" >&2
fi
exit "$rc"

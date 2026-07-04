#!/bin/sh
# ============================================================================
# cadences/import_collections.sh — bookkeeping ingestion for catalog media.
#
# Walks raw/collections/ for Steam/Goodreads/IMDB/generic export files and
# creates one entity per row in entities/collection/<slug>.md. Idempotent.
# Not a recurring cadence — run it when you drop a new export file in.
#
# Usage:  import_collections.sh [VAULT_DIR] [--source FILE_OR_DIR] [--dry-run]
# ============================================================================
set -eu

. "$(cd "$(dirname "$0")" && pwd)/_common.sh"

# Distinguish vault arg from passthrough flags: the vault arg, if present,
# is the FIRST argument AND does not start with '-'. Anything else is a
# passthrough flag to collections_importer.py.
if [ "$#" -gt 0 ] && [ "${1#-}" = "$1" ]; then
    VAULT_ARG="$1"
    shift
else
    VAULT_ARG=""
fi
resolve_vault "$VAULT_ARG"

start=$(date +%s)

# Importer failure must not skip validate (nor the run record) — capture rc.
imp_rc=0
python3 -m agent_vault.collections_importer . "$@" || imp_rc=$?

val_rc=0
python3 -m agent_vault.validate . >/dev/null || val_rc=$?

rc=0
[ "$val_rc" -ne 0 ] && rc="$val_rc"
[ "$imp_rc" -ne 0 ] && rc="$imp_rc"   # importer failure outranks validate findings

record_run import_collections "$rc" "$(( $(date +%s) - start ))" "importer_rc=$imp_rc validate_rc=$val_rc"
if [ "$rc" -eq 0 ]; then
    echo "import_collections: ok ($(date -u +%FT%TZ))"
else
    echo "import_collections: importer_rc=$imp_rc validate_rc=$val_rc" >&2
fi
exit "$rc"

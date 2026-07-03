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

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

# Distinguish vault arg from passthrough flags: the vault arg, if present,
# is the FIRST argument AND does not start with '-'. Anything else is a
# passthrough flag to collections_importer.py.
if [ "$#" -gt 0 ] && [ "${1#-}" = "$1" ]; then
    VAULT="$1"
    shift
else
    VAULT="$(dirname "$SCRIPT_DIR")"
fi

cd "$VAULT"
python3 -m agent_vault.collections_importer . "$@"
python3 -m agent_vault.validate . >/dev/null
echo "import_collections: ok ($(date -u +%FT%TZ))"

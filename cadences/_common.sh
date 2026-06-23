# _common.sh — shared helpers for the cadence scripts (POSIX sh, sourced).
#
# Provides:
#   resolve_vault "$1"   -> sets VAULT, verifies it's a real vault, cd's into it
#   record_run cadence rc duration_s detail
#                        -> appends one JSON line to discovery/_runs.jsonl
#
# Sourced, not executed. No `set -e` here (the caller owns that).

resolve_vault() {
    _script_dir="$(cd "$(dirname "$0")" && pwd)"
    VAULT="${1:-$(dirname "$_script_dir")}"
    if [ ! -d "$VAULT/registry" ]; then
        echo "error: '$VAULT' is not an Agent Vault (no registry/ dir)" >&2
        exit 2
    fi
    cd "$VAULT" || { echo "error: cannot cd into '$VAULT'" >&2; exit 2; }
}

# record_run <cadence> <rc> <duration_s> <detail>
# Always best-effort; never aborts the caller.
record_run() {
    mkdir -p discovery 2>/dev/null || true
    _ts="$(date -u +%FT%TZ)"
    # detail is free text; strip backslashes/quotes/newlines so the JSON line
    # stays valid (a stray backslash would form an illegal JSON escape).
    _detail="$(printf '%s' "$4" | tr -d '\\"\n\r\t' | cut -c1-300)"
    printf '{"cadence":"%s","ts":"%s","rc":%s,"duration_s":%s,"detail":"%s"}\n' \
        "$1" "$_ts" "$2" "$3" "$_detail" >> discovery/_runs.jsonl 2>/dev/null || true
}

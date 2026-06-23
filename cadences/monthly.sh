#!/bin/sh
# ============================================================================
# cadences/monthly.sh — read-only audit.
#
# Runs the schema validator AND the lint pass (broken refs, aging review items,
# compile drift, manifest mismatches). Writes nothing back to the vault except
# the lint report + a run record. Exit code is non-zero if validate or lint
# found anything — cron / systemd treats that as a notification trigger.
#
# Usage:  monthly.sh [VAULT_DIR]
# ============================================================================
set -eu

. "$(cd "$(dirname "$0")" && pwd)/_common.sh"
resolve_vault "${1:-}"

start=$(date +%s)

# 1. Schema validation — capture (don't abort) so lint still runs on a broken
#    vault (the lint report is exactly what the operator wants then).
val_rc=0
python3 validate.py . || val_rc=$?

# 2. Lint pass. Read-only; report always stored for tooling.
lint_rc=0
python3 lint.py . --report discovery/_lint_report.json || lint_rc=$?

rc=0
[ "$lint_rc" -ne 0 ] && rc="$lint_rc"
[ "$val_rc" -ne 0 ] && rc="$val_rc"   # validate failure outranks lint findings

issues="?"
if [ -f discovery/_lint_report.json ]; then
    issues=$(python3 -c 'import json;print(json.load(open("discovery/_lint_report.json"))["total_issues"])' 2>/dev/null || echo "?")
fi

record_run monthly "$rc" "$(( $(date +%s) - start ))" "validate_rc=$val_rc lint_rc=$lint_rc issues=$issues"
if [ "$rc" -eq 0 ]; then
    echo "monthly: ok ($(date -u +%FT%TZ))"
else
    echo "monthly: validate_rc=$val_rc lint_rc=$lint_rc — $issues lint issue(s) — see discovery/_lint_report.json" >&2
fi
exit "$rc"

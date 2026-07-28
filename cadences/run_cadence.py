#!/usr/bin/env python3
"""
cadences/run_cadence.py — thin shim over agent_vault.cadence.

Historical entry point for the cross-platform cadence runner. The real
implementation now lives in agent_vault/cadence.py (installable, exposes the
agent-vault-cadence console script). This file is kept so existing callers —
`python cadences/run_cadence.py daily .`, the .sh wrappers, and the test
suite (which imports run_cadence._sanitize_detail) — keep working unchanged.

Usage (unchanged):
    python cadences/run_cadence.py <kind> [vault_dir] [--dry-run]
"""
import os
import sys

# When invoked as a bare script (python cadences/run_cadence.py ...), the
# agent_vault package may not be on sys.path if it isn't pip-installed. Insert
# the repo root (parent of this cadences/ dir) so the import always resolves.
_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from agent_vault import cadence as _cadence  # noqa: E402

# Re-export every name so `from run_cadence import _sanitize_detail` and
# attribute access (run_cadence.run_daily, etc.) still work for old callers.
resolve_vault = _cadence.resolve_vault
run_stage = _cadence.run_stage
run_stage_suppress_stdout = _cadence.run_stage_suppress_stdout
_sanitize_detail = _cadence._sanitize_detail
record_run = _cadence.record_run
run_cadence = _cadence.run_cadence
_print_plan = _cadence._print_plan
VALID_KINDS = _cadence.VALID_KINDS
main = _cadence.main


if __name__ == "__main__":
    main()

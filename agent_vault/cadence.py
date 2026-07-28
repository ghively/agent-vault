#!/usr/bin/env python3
"""
agent_vault/cadence.py — Phase 5 cadence runner.

Sequences the vault's pipeline modules (ingest, compiler, promote, validate,
lint) into three scheduled cadences. Each is a clean CLI entry point that the
runner (Hermes cron, systemd, n8n) schedules — swapping schedulers changes one
line.

Cadences:
    daily    — ingest + validate (cheap, no LLM, safe hourly)
    weekly   — ingest + compile + promote + validate (the LLM touchpoint)
    monthly  — validate + lint (read-only audit, writes a report)

Usage:
    agent-vault-cadence <kind> [vault_dir] [--dry-run]

    kind      : daily | weekly | monthly
    vault_dir : optional; defaults to cwd. Must contain a registry/ dir.
    --dry-run : print the plan without side effects (no stages execute)

Exit codes:
    0  — cadence completed without errors
    N  — last failing stage's exit code
    2  — bad arguments or vault not found

The cadences/run_cadence.py cross-platform runner is now a thin shim that
delegates here. The .sh wrappers continue to work unchanged.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from datetime import datetime, timezone

# Force UTF-8 stdout/stderr so Unicode chars don't crash on Windows consoles
# that default to cp1252. Mirrors the idiom in ingest.py / compiler.py.
for _s in (sys.stdout, sys.stderr):
    if hasattr(_s, "reconfigure"):
        _s.reconfigure(encoding="utf-8")

# ---------------------------------------------------------------------------
# constants
# ---------------------------------------------------------------------------

VALID_KINDS = {"daily", "weekly", "monthly"}

# Stages each cadence runs, in order. Each entry is:
#   (module, suppress_stdout, extra_args)
# In --dry-run mode these are printed as a plan but never executed.
#
# Daily starts with the Phase 4 feeder (inbox -> raw/), then ingests + validates.
# Weekly re-runs ingest (catches anything the feeder missed) before compile.
# Monthly is read-only (validate + lint).
_STAGES: dict[str, list[tuple[str, bool, list[str]]]] = {
    "daily": [
        ("feeders.watch", False, ["--once"]),
        ("validate", True, []),
    ],
    "weekly": [
        ("feeders.watch", False, ["--once"]),
        ("compiler", False, []),
        ("promote", False, []),
        ("validate", True, []),
    ],
    "monthly": [
        ("validate", False, []),
        ("lint", False, ["--report", "discovery/_lint_report.json"]),
    ],
}


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def resolve_vault(vault_arg: str) -> str:
    """
    Return an absolute path to the vault root, validate it has registry/,
    then os.chdir() into it so stage scripts can be invoked with '.' as the
    vault dir. Prints to stderr and sys.exit(2) on any problem.
    """
    vault = os.path.abspath(vault_arg) if vault_arg else os.path.abspath(".")
    if not os.path.isdir(os.path.join(vault, "registry")):
        print(
            f"error: '{vault}' is not an Agent Vault (no registry/ dir)",
            file=sys.stderr,
        )
        sys.exit(2)
    os.chdir(vault)
    return vault


def run_stage(module: str, extra_args: list[str] | None = None) -> int:
    """
    Run  sys.executable -m agent_vault.<module> .  (plus any extra_args) as a
    subprocess. Inherits the full parent environment. Returns the exit code.
    """
    cmd = [sys.executable, "-m", f"agent_vault.{module}", "."] + (extra_args or [])
    result = subprocess.run(cmd)
    return result.returncode


def run_stage_suppress_stdout(module: str, extra_args: list[str] | None = None) -> int:
    """Same as run_stage() but suppresses the child's stdout."""
    cmd = [sys.executable, "-m", f"agent_vault.{module}", "."] + (extra_args or [])
    result = subprocess.run(cmd, stdout=subprocess.DEVNULL)
    return result.returncode


def _sanitize_detail(text: str) -> str:
    """Strip chars that would break a JSON string literal, truncate to 300."""
    for ch in ("\\", '"', "\n", "\r", "\t"):
        text = text.replace(ch, "")
    return text[:300]


def record_run(kind: str, rc: int, duration_s: int, detail: str) -> None:
    """Best-effort: append one JSON line to discovery/_runs.jsonl; never raises."""
    try:
        os.makedirs("discovery", exist_ok=True)
        ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        safe_detail = _sanitize_detail(detail)
        line = json.dumps(
            {
                "cadence": kind,
                "ts": ts,
                "rc": rc,
                "duration_s": duration_s,
                "detail": safe_detail,
            }
        )
        with open("discovery/_runs.jsonl", "a", encoding="utf-8") as fh:
            fh.write(line + "\n")
    except Exception:  # noqa: BLE001
        pass  # best-effort; never abort the caller


# ---------------------------------------------------------------------------
# dry-run plan
# ---------------------------------------------------------------------------

def _print_plan(kind: str, vault: str) -> None:
    """Print what the cadence would do, without executing anything."""
    stages = _STAGES[kind]
    llm = kind == "weekly"
    print(f"[DRY RUN] {kind} cadence — vault: {vault}")
    print(f"  LLM call: {'yes (compiler.py)' if llm else 'no'}")
    print(f"  stages ({len(stages)}):")
    for i, (module, _suppress, extra) in enumerate(stages, 1):
        suffix = f" {' '.join(extra)}" if extra else ""
        print(f"    {i}. python -m agent_vault.{module} .{suffix}")
    print("  no side effects — nothing executed, nothing written.")


# ---------------------------------------------------------------------------
# cadence implementations
# ---------------------------------------------------------------------------

def run_cadence(kind: str, dry_run: bool = False) -> tuple[int, str]:
    """
    Execute a cadence (or print its plan if dry_run). Returns (rc, detail).
    Every stage runs regardless of earlier failures; rc = last failing rc.
    """
    if dry_run:
        vault = os.getcwd()
        _print_plan(kind, vault)
        return 0, "dry-run"

    rc = 0
    detail = ""

    for module, suppress, extra in _STAGES[kind]:
        if suppress:
            stage_rc = run_stage_suppress_stdout(module, extra_args=extra or None)
        else:
            stage_rc = run_stage(module, extra_args=extra or None)
        if stage_rc != 0:
            rc = stage_rc
            detail += f" {module}(rc={stage_rc})"

    # Monthly: read issue count from the lint report this run wrote.
    if kind == "monthly":
        report_path = "discovery/_lint_report.json"
        issues: str | int = "?"
        if os.path.isfile(report_path):
            try:
                with open(report_path, encoding="utf-8") as fh:
                    data = json.load(fh)
                issues = data["total_issues"]
            except Exception:  # noqa: BLE001
                issues = "?"
        detail = detail.strip() or f"validate=ok lint=ok issues={issues}"
        if rc == 0:
            detail = f"validate=ok lint=ok issues={issues}"

    return rc, detail.strip()


# ---------------------------------------------------------------------------
# entry point
# ---------------------------------------------------------------------------

def main() -> None:
    args = sys.argv[1:]
    if not args:
        print(
            "usage: agent-vault-cadence <kind> [vault_dir] [--dry-run]\n"
            "       kind: daily | weekly | monthly",
            file=sys.stderr,
        )
        sys.exit(2)

    # Parse args: first positional = kind, second positional = vault, --dry-run flag
    dry_run = False
    positional: list[str] = []
    for a in args:
        if a == "--dry-run":
            dry_run = True
        elif a in ("-h", "--help"):
            print(__doc__)
            sys.exit(0)
        elif not a.startswith("-"):
            positional.append(a)
        else:
            print(f"error: unknown flag '{a}'", file=sys.stderr)
            sys.exit(2)

    if not positional:
        print("error: missing <kind> (daily | weekly | monthly)", file=sys.stderr)
        sys.exit(2)

    kind = positional[0]
    vault_arg = positional[1] if len(positional) > 1 else ""

    if kind not in VALID_KINDS:
        print(
            f"error: unknown cadence kind '{kind}'. "
            f"Must be one of: {', '.join(sorted(VALID_KINDS))}",
            file=sys.stderr,
        )
        sys.exit(2)

    resolve_vault(vault_arg)

    t_start = time.time()
    rc, detail = run_cadence(kind, dry_run=dry_run)
    duration_s = int(time.time() - t_start)

    if not dry_run:
        record_run(kind, rc, duration_s, detail if detail else "ok")

    if dry_run:
        # Plan already printed; nothing more to do.
        pass
    elif rc == 0:
        ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        print(f"{kind}: ok ({ts})")
    else:
        ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        print(
            f"{kind}: FAILED ({detail}) — see discovery/_runs.jsonl",
            file=sys.stderr,
        )

    sys.exit(rc)


if __name__ == "__main__":
    main()

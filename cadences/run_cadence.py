#!/usr/bin/env python3
"""
cadences/run_cadence.py — cross-platform replacement for the .sh cadence wrappers.

Sequences the agent_vault package's pipeline modules (agent_vault.ingest,
agent_vault.compiler, agent_vault.promote, agent_vault.validate,
agent_vault.lint) via `python -m` subprocess calls so the pipeline runs on
Windows as well as Linux, without requiring `sh`.  The .sh files are kept
unchanged for the Linux appliance; this file is the Windows / server path.

Usage:
    python cadences/run_cadence.py <kind> [vault_dir]

    kind      : daily | weekly | monthly
    vault_dir : optional; defaults to cwd.  Must contain a registry/ dir.

Exit codes:
    0  — cadence completed without errors
    N  — last failing stage's exit code (matches .sh semantics)
    2  — bad arguments or vault not found
"""
import json
import os
import subprocess
import sys
import time
from datetime import datetime, timezone

# Force UTF-8 stdout/stderr so Unicode chars don't crash on Windows consoles
# that default to cp1252.  Mirrors the idiom in agent_vault/ingest.py / compiler.py.
for _s in (sys.stdout, sys.stderr):
    if hasattr(_s, "reconfigure"):
        _s.reconfigure(encoding="utf-8")

# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

VALID_KINDS = {"daily", "weekly", "monthly"}


def resolve_vault(vault_arg: str) -> str:
    """
    Return an absolute path to the vault root, validate it has registry/,
    then os.chdir() into it so stage scripts can be invoked with '.' as the
    vault dir (e.g. 'python -m agent_vault.ingest .').
    Prints to stderr and sys.exit(2) on any problem (matches _common.sh).
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
    subprocess. Inherits the full parent environment (so AGENT_VAULT_COMPILER /
    OLLAMA_* are visible to the child, matching .sh behaviour). Requires the
    agent_vault package to be importable (installed via `pip install -e .`, or
    cwd is the repo containing it) — the pipeline scripts live under
    agent_vault/, not as bare files in the vault dir.
    Returns the exit code; never raises.
    """
    cmd = [sys.executable, "-m", f"agent_vault.{module}", "."] + (extra_args or [])
    result = subprocess.run(cmd)
    return result.returncode


def run_stage_suppress_stdout(module: str, extra_args: list[str] | None = None) -> int:
    """
    Same as run_stage() but suppresses the child's stdout (agent_vault.validate
    can be chatty; the .sh cadences pipe it to /dev/null).
    """
    cmd = [sys.executable, "-m", f"agent_vault.{module}", "."] + (extra_args or [])
    result = subprocess.run(cmd, stdout=subprocess.DEVNULL)
    return result.returncode


def _sanitize_detail(text: str) -> str:
    """Strip chars that would break a JSON string literal, truncate to 300."""
    for ch in ("\\", '"', "\n", "\r", "\t"):
        text = text.replace(ch, "")
    return text[:300]


def record_run(kind: str, rc: int, duration_s: int, detail: str) -> None:
    """
    Best-effort port of _common.sh record_run.
    Appends one JSON line to discovery/_runs.jsonl; never raises.
    """
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
# cadence implementations
# ---------------------------------------------------------------------------

def run_daily() -> tuple[int, str]:
    """
    Cheap, no-LLM cadence.
    1. agent_vault.ingest .
    2. agent_vault.validate . (stdout suppressed)
    rc = last failing stage's rc; detail accumulates failures.
    """
    rc = 0
    detail = ""

    stage_rc = run_stage("ingest")
    if stage_rc != 0:
        rc = stage_rc
        detail += f" ingest(rc={stage_rc})"

    stage_rc = run_stage_suppress_stdout("validate")
    if stage_rc != 0:
        rc = stage_rc
        detail += f" validate(rc={stage_rc})"

    return rc, detail


def run_weekly() -> tuple[int, str]:
    """
    LLM touchpoint cadence.
    1. agent_vault.ingest .
    2. agent_vault.compiler .
    3. agent_vault.promote .
    4. agent_vault.validate . (stdout suppressed)
    Every step runs regardless of earlier failures; rc = last failing step.
    """
    rc = 0
    detail = ""

    stage_rc = run_stage("ingest")
    if stage_rc != 0:
        rc = stage_rc
        detail += f" ingest(rc={stage_rc})"

    stage_rc = run_stage("compiler")
    if stage_rc != 0:
        rc = stage_rc
        detail += f" compile(rc={stage_rc})"

    stage_rc = run_stage("promote")
    if stage_rc != 0:
        rc = stage_rc
        detail += f" promote(rc={stage_rc})"

    stage_rc = run_stage_suppress_stdout("validate")
    if stage_rc != 0:
        rc = stage_rc
        detail += f" validate(rc={stage_rc})"

    return rc, detail


def run_monthly() -> tuple[int, str]:
    """
    Read-only audit cadence.
    1. agent_vault.validate .       — capture rc, don't abort
    2. agent_vault.lint . --report discovery/_lint_report.json — capture rc
    rc: lint_rc set first, then validate overrides (validate outranks lint).
    """
    val_rc = run_stage("validate")
    report_path = "discovery/_lint_report.json"
    lint_started = time.time()
    lint_rc = run_stage("lint", extra_args=["--report", report_path])

    # validate outranks lint (mirrors: [ "$val_rc" -ne 0 ] && rc="$val_rc")
    rc = lint_rc if lint_rc != 0 else 0
    if val_rc != 0:
        rc = val_rc

    # read total_issues from the lint report — but only if THIS run wrote it;
    # a report left over from a previous run must not be presented as current
    # (1s slack for filesystems with coarse mtime granularity)
    issues: str | int = "?"
    if os.path.isfile(report_path) and os.path.getmtime(report_path) >= lint_started - 1:
        try:
            with open(report_path, encoding="utf-8") as fh:
                data = json.load(fh)
            issues = data["total_issues"]
        except Exception:  # noqa: BLE001
            issues = "?"

    detail = f"validate_rc={val_rc} lint_rc={lint_rc} issues={issues}"
    return rc, detail


# ---------------------------------------------------------------------------
# entry point
# ---------------------------------------------------------------------------

def main() -> None:
    args = sys.argv[1:]
    if not args:
        print(
            "usage: run_cadence.py <kind> [vault_dir]\n"
            "       kind: daily | weekly | monthly",
            file=sys.stderr,
        )
        sys.exit(2)

    kind = args[0]
    vault_arg = args[1] if len(args) > 1 else ""

    if kind not in VALID_KINDS:
        print(
            f"error: unknown cadence kind '{kind}'. "
            f"Must be one of: {', '.join(sorted(VALID_KINDS))}",
            file=sys.stderr,
        )
        sys.exit(2)

    resolve_vault(vault_arg)

    t_start = time.time()

    if kind == "daily":
        rc, detail = run_daily()
    elif kind == "weekly":
        rc, detail = run_weekly()
    else:  # monthly
        rc, detail = run_monthly()

    duration_s = int(time.time() - t_start)
    record_run(kind, rc, duration_s, detail.strip() if detail.strip() else "ok")

    ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    if rc == 0:
        print(f"{kind}: ok ({ts})")
    else:
        print(
            f"{kind}: FAILED ({detail.strip()}) — see discovery/_runs.jsonl",
            file=sys.stderr,
        )

    sys.exit(rc)


if __name__ == "__main__":
    main()

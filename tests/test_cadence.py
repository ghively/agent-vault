"""B3 — end-to-end coverage for cadences/run_cadence.py (the cross-platform
runner that writes the discovery/_runs.jsonl records the dashboard depends on).

Runs it as a real subprocess (its actual usage) with the offline mock compiler,
so Ollama is never a test dependency.
"""

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

RUNNER = Path(__file__).resolve().parents[1] / "cadences" / "run_cadence.py"


def _vault(tmp_path: Path) -> Path:
    vault = tmp_path / "v"
    (vault / "entities" / "asset").mkdir(parents=True)
    (vault / "raw").mkdir()
    (vault / "discovery").mkdir()
    shutil.copytree("registry", vault / "registry")
    (vault / "entities" / "asset" / "f.md").write_text(
        "---\nslug: f\ntype: asset\nsubtype: hvac\ntitle: F\nstatus: compiled\n"
        "confidence: 1.0\ncreated: 2026-01-01\nsources: []\nsources_hash: h\n"
        "compiled_from_hash: h\n---\n\n<!-- LINKS:BEGIN -->\n<!-- LINKS:END -->\n\n"
        "A furnace.\n", encoding="utf-8")
    return vault


def _run(kind: str, vault: Path):
    env = {**os.environ, "AGENT_VAULT_COMPILER": "mock"}
    return subprocess.run(
        [sys.executable, str(RUNNER), kind, str(vault)],
        env=env, capture_output=True, text=True, timeout=120,
    )


def _runs(vault: Path) -> list[dict]:
    p = vault / "discovery" / "_runs.jsonl"
    if not p.exists():
        return []
    return [json.loads(x) for x in p.read_text().splitlines() if x.strip()]


def test_daily_cadence_runs_and_records(tmp_path):
    vault = _vault(tmp_path)
    r = _run("daily", vault)
    assert r.returncode == 0, r.stderr
    recs = _runs(vault)
    assert recs, "daily must append a _runs.jsonl record"
    rec = recs[-1]
    # Shape the dashboard/history read (status.py:_last_run / history.get_runs).
    assert rec["cadence"] == "daily"
    assert rec["rc"] == 0
    assert set(rec.keys()) >= {"cadence", "ts", "rc", "duration_s", "detail"}
    assert isinstance(rec["duration_s"], int)


def test_weekly_cadence_runs_with_mock_compiler(tmp_path):
    vault = _vault(tmp_path)
    r = _run("weekly", vault)
    assert r.returncode == 0, r.stderr
    recs = _runs(vault)
    assert recs[-1]["cadence"] == "weekly"
    assert recs[-1]["rc"] == 0


def test_unknown_kind_exits_2(tmp_path):
    vault = _vault(tmp_path)
    r = _run("bogus", vault)
    assert r.returncode == 2
    assert "unknown cadence kind" in r.stderr


def test_record_never_raises_on_bad_detail(tmp_path):
    """record_run sanitizes detail and is best-effort (never aborts the run)."""
    sys.path.insert(0, str(RUNNER.parent))
    try:
        import run_cadence  # type: ignore
    finally:
        sys.path.pop(0)
    assert run_cadence._sanitize_detail('a"b\nc\\d') == "abcd"
    assert len(run_cadence._sanitize_detail("x" * 500)) == 300


# ---------------------------------------------------------------------------
# Phase 5: --dry-run + console script tests
# ---------------------------------------------------------------------------

# The installable package module is the canonical implementation now.
PKG_MODULE = "agent_vault.cadence"
PKG_ENTRY = Path(__file__).resolve().parents[1] / "agent_vault" / "cadence.py"


def _run_with_args(kind: str, vault: Path, *extra: str):
    env = {**os.environ, "AGENT_VAULT_COMPILER": "mock"}
    return subprocess.run(
        [sys.executable, str(RUNNER), kind, str(vault), *extra],
        env=env, capture_output=True, text=True, timeout=120,
    )


def test_daily_dry_run_prints_plan_and_writes_nothing(tmp_path):
    """--dry-run must print the plan and have zero side effects.

    No _runs.jsonl record, no stage output — just the plan."""
    vault = _vault(tmp_path)
    r = _run_with_args("daily", vault, "--dry-run")
    assert r.returncode == 0, r.stderr
    assert "[DRY RUN]" in r.stdout
    assert "daily" in r.stdout
    assert "feeders.watch" in r.stdout  # Phase 4 feeder is the first daily stage
    assert "validate" in r.stdout
    assert "no side effects" in r.stdout
    # No run record written (the whole point of dry-run).
    assert _runs(vault) == [], "dry-run must not write a _runs.jsonl record"


def test_weekly_dry_run_shows_llm_stage(tmp_path):
    """Weekly dry-run must flag the LLM touchpoint (compiler.py)."""
    vault = _vault(tmp_path)
    r = _run_with_args("weekly", vault, "--dry-run")
    assert r.returncode == 0, r.stderr
    assert "[DRY RUN]" in r.stdout
    assert "LLM call: yes" in r.stdout
    assert "compiler" in r.stdout
    assert "promote" in r.stdout
    assert "feeders.watch" in r.stdout  # weekly starts with the feeder too
    assert _runs(vault) == []


def test_monthly_dry_run_shows_lint_report_flag(tmp_path):
    """Monthly dry-run must show the lint --report flag in the plan."""
    vault = _vault(tmp_path)
    r = _run_with_args("monthly", vault, "--dry-run")
    assert r.returncode == 0, r.stderr
    assert "[DRY RUN]" in r.stdout
    assert "LLM call: no" in r.stdout
    assert "lint" in r.stdout
    assert "--report" in r.stdout
    assert _runs(vault) == []


def test_dry_run_no_llm_call(tmp_path):
    """Daily and monthly are LLM-free; weekly's compiler is the only LLM stage.
    In dry-run, even weekly must NOT invoke the LLM — the plan is static."""
    vault = _vault(tmp_path)
    for kind in ("daily", "weekly", "monthly"):
        r = _run_with_args(kind, vault, "--dry-run")
        assert r.returncode == 0, (kind, r.stderr)
        assert "[DRY RUN]" in r.stdout


def test_console_script_registered():
    """pyproject.toml must declare the agent-vault-cadence entry point."""
    pyproject = Path(__file__).resolve().parents[1] / "pyproject.toml"
    text = pyproject.read_text(encoding="utf-8")
    assert 'agent-vault-cadence = "agent_vault.cadence:main"' in text, \
        "pyproject.toml missing agent-vault-cadence console script"


def test_package_module_importable():
    """agent_vault.cadence must import cleanly and expose run_cadence()."""
    import importlib
    mod = importlib.import_module("agent_vault.cadence")
    assert callable(mod.run_cadence)
    assert callable(mod.main)
    assert "daily" in mod.VALID_KINDS


def test_shim_re_exports_all_names():
    """cadences/run_cadence.py shim must re-export the canonical module's names
    so old callers (and the test above) keep working."""
    sys.path.insert(0, str(RUNNER.parent))
    try:
        import run_cadence  # type: ignore
    finally:
        sys.path.pop(0)
    assert run_cadence._sanitize_detail('a"b\nc\\d') == "abcd"
    assert callable(run_cadence.resolve_vault)
    assert callable(run_cadence.run_stage)
    assert callable(run_cadence.record_run)
    assert callable(run_cadence.run_cadence)

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

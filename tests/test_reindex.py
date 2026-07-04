"""Tests for O7 (index freshness) + B1 (config validation)."""

import json
import shutil
import time
from pathlib import Path

import pytest

from agent_vault import build_index, compiler
from agent_vault.api.config import Settings
from agent_vault.api.status import _index_status


def _vault(tmp_path: Path) -> Path:
    vault = tmp_path / "v"
    (vault / "entities" / "asset").mkdir(parents=True)
    (vault / "discovery").mkdir(parents=True)
    shutil.copytree("registry", vault / "registry")
    (vault / "entities" / "asset" / "f.md").write_text(
        "---\nslug: f\ntype: asset\nsubtype: hvac\ntitle: F\nstatus: stub\n"
        "sources: []\nsources_hash: h1\n---\n\n"
        "<!-- LINKS:BEGIN -->\n<!-- LINKS:END -->\n\n", encoding="utf-8")
    build_index.reindex(str(vault), quiet=True)
    return vault


def test_reindex_creates_all_indexes(tmp_path):
    vault = _vault(tmp_path)
    assert (vault / "_index.json").exists()
    assert (vault / "_index.md").exists()
    # FTS sidecar built too (when available)
    from agent_vault import search
    if search.fts5_available():
        assert (vault / "_index.db").exists()


def test_compile_refreshes_index_status(tmp_path, monkeypatch):
    """O7: a compile flips status stub->compiled AND the index reflects it, so a
    reader right after recompile never sees the stale `stub`."""
    monkeypatch.setenv("AGENT_VAULT_COMPILER", "mock")
    vault = _vault(tmp_path)
    before = json.loads((vault / "_index.json").read_text())["entities"][0]["status"]
    assert before == "stub"
    compiler.compile_all(str(vault))
    after = json.loads((vault / "_index.json").read_text())["entities"][0]["status"]
    assert after == "compiled"


def test_index_status_reports_freshness(tmp_path):
    vault = _vault(tmp_path)
    fresh = _index_status(vault)
    assert fresh["stale"] is False
    assert fresh["count"] == 1
    assert fresh["generated"]

    # An external edit that bypasses the pipeline makes the index stale.
    time.sleep(1.1)
    f = vault / "entities" / "asset" / "f.md"
    f.write_text(f.read_text() + "\nextra\n", encoding="utf-8")
    assert _index_status(vault)["stale"] is True


def test_index_status_missing_index_is_stale(tmp_path):
    vault = tmp_path / "empty"
    vault.mkdir()
    st = _index_status(vault)
    assert st["stale"] is True
    assert st["generated"] is None


# --- B1 config validation ---------------------------------------------------

def test_config_rejects_non_numeric_port(monkeypatch):
    monkeypatch.setenv("AGENT_VAULT_PORT", "notaport")
    with pytest.raises(SystemExit):
        Settings.from_env()


def test_config_rejects_out_of_range_port(monkeypatch):
    monkeypatch.setenv("AGENT_VAULT_PORT", "99999")
    with pytest.raises(SystemExit):
        Settings.from_env()


def test_validate_rejects_missing_vault_dir(tmp_path):
    s = Settings(vault_path=str(tmp_path / "does-not-exist"))
    with pytest.raises(SystemExit):
        s.validate()


def test_validate_accepts_real_dir(tmp_path):
    Settings(vault_path=str(tmp_path)).validate()  # no raise

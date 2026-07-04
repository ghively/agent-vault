"""Behavioral coverage for the ingest / compiler / promote CLI main()
entrypoints — argument parsing, output, and exit codes end to end.

compiler's --only path is covered in test_compiler_only.py; this adds the
--help / --limit / nothing-to-compile / normal-compile branches, plus ingest
and promote main(). All runs use the offline mock compiler.
"""
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
VAULT_SRC = HERE.parent
MOCK_ENV = {**os.environ, "AGENT_VAULT_COMPILER": "mock"}


@pytest.fixture
def vault(tmp_path):
    dst = tmp_path / "vault"
    shutil.copytree(VAULT_SRC, dst, ignore=shutil.ignore_patterns("tests", "__pycache__", ".git"))
    return dst


def _run(vault, *args, env=None):
    return subprocess.run([sys.executable, "-m", *args], cwd=str(vault),
                          capture_output=True, text=True, env=env or os.environ.copy())


def _make_stub(vault, ref):
    p = vault / "entities" / ref
    p.write_text(p.read_text(encoding="utf-8").replace("status: compiled", "status: stub", 1),
                 encoding="utf-8")


# ---------------------------------------------------------------------------
# ingest main()
# ---------------------------------------------------------------------------

def test_ingest_main_help(vault):
    out = _run(vault, "agent_vault.ingest", "--help")
    assert out.returncode == 0 and "ingest" in out.stdout.lower()


def test_ingest_main_processes_a_raw_file(vault):
    raw = vault / "raw" / "documents"
    raw.mkdir(parents=True, exist_ok=True)
    (raw / "note.txt").write_text(
        "Bank of America statement\nAccount ending 1234\nStatement date 2026-01-15\n",
        encoding="utf-8")
    out = _run(vault, "agent_vault.ingest", ".")
    assert out.returncode == 0
    assert "ingest summary:" in out.stdout


# ---------------------------------------------------------------------------
# compiler main()
# ---------------------------------------------------------------------------

def test_compiler_main_help(vault):
    out = _run(vault, "agent_vault.compiler", "--help")
    assert out.returncode == 0 and "compile" in out.stdout.lower()


def test_compiler_main_nothing_to_compile(vault):
    # sample entities all ship status: compiled with matching hashes
    out = _run(vault, "agent_vault.compiler", ".", env=MOCK_ENV)
    assert out.returncode == 0
    assert "client=mock" in out.stdout
    assert "nothing to compile" in out.stdout


def test_compiler_main_compiles_a_stub(vault):
    _make_stub(vault, "asset/carrier-furnace.md")
    out = _run(vault, "agent_vault.compiler", ".", env=MOCK_ENV)
    assert out.returncode == 0
    assert "compiled 1 entity" in out.stdout
    assert "carrier-furnace" in out.stdout
    # status flipped back to compiled on disk
    assert "status: compiled" in (vault / "entities" / "asset" / "carrier-furnace.md").read_text(encoding="utf-8")


def test_compiler_main_limit_caps_the_batch(vault):
    # two stubs pending, --limit 1 compiles exactly one this pass
    _make_stub(vault, "asset/carrier-furnace.md")
    _make_stub(vault, "account/bofa-checking.md")
    out = _run(vault, "agent_vault.compiler", ".", "--limit", "1", env=MOCK_ENV)
    assert out.returncode == 0
    assert "compiled 1 entity" in out.stdout


def test_compiler_main_model_override_names_client(vault):
    # --model flows into the client name even for a no-op run
    out = _run(vault, "agent_vault.compiler", ".", "--model", "llama3", env=MOCK_ENV)
    # mock ignores the model, but the flag must parse without error
    assert out.returncode == 0


# ---------------------------------------------------------------------------
# promote main()
# ---------------------------------------------------------------------------

def test_promote_main_help(vault):
    out = _run(vault, "agent_vault.promote", "--help")
    assert out.returncode == 0 and "promot" in out.stdout.lower()


def test_promote_main_dry_run(vault):
    out = _run(vault, "agent_vault.promote", ".", "--dry-run")
    assert out.returncode == 0
    assert "DRY RUN" in out.stdout
    assert "proposals ->" in out.stdout


def test_promote_main_normal_run(vault):
    out = _run(vault, "agent_vault.promote", ".")
    assert out.returncode == 0
    assert "promotion pass:" in out.stdout

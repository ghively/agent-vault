"""--only compiles a single targeted entity. Uses the MockClient (offline)."""
import os
import shutil
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
VAULT_SRC = HERE.parent


def _sandbox(tmp_path):
    dst = tmp_path / "vault"
    shutil.copytree(VAULT_SRC, dst, ignore=shutil.ignore_patterns("tests", "__pycache__"))
    return dst


def test_only_compiles_just_one_entity(tmp_path):
    vault = _sandbox(tmp_path)
    # Make two entities into stubs so both are pending.
    for ref in ("asset/carrier-furnace.md", "account/bofa-checking.md"):
        p = vault / "entities" / ref
        text = p.read_text(encoding="utf-8").replace("status: compiled", "status: stub", 1)
        p.write_text(text, encoding="utf-8")
    env = {**os.environ, "AGENT_VAULT_COMPILER": "mock"}
    out = subprocess.run([sys.executable, "compiler.py", ".", "--only", "carrier-furnace"],
                         cwd=str(vault), capture_output=True, text=True, env=env)
    assert out.returncode == 0, out.stderr
    assert "carrier-furnace" in out.stdout
    assert "bofa-checking" not in out.stdout  # the other stub was NOT touched

"""validate.py is the smoke test every stage reuses. Confirm it passes on shipped
data (warnings don't fail) and that a real schema violation makes it exit 1."""
import re
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


def _run(vault):
    return subprocess.run(
        [sys.executable, "validate.py", "."],
        cwd=str(vault),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )


def test_validate_passes_on_shipped_data(tmp_path):
    vault = _sandbox(tmp_path)
    out = _run(vault)
    assert out.returncode == 0, out.stdout + out.stderr


def test_validate_fails_on_invalid_status(tmp_path):
    vault = _sandbox(tmp_path)
    target = next((vault / "entities").glob("*/*.md"))
    text = target.read_text(encoding="utf-8")
    corrupted = re.sub(r"(?m)^status:.*$", "status: bogus-status", text, count=1)
    assert corrupted != text, "expected a status line to rewrite"
    target.write_text(corrupted, encoding="utf-8")
    out = _run(vault)
    assert out.returncode == 1
    assert "FAIL" in out.stdout

"""Behavioral coverage for the lint / compact / review CLI main() entrypoints.

test_console_script.py and test_package.py confirm the modules are wired and
importable; these drive their main() argument parsing, output, and exit codes
end to end via subprocess against a sandbox vault. (validate / build_index /
compiler / collections_importer main() are covered in their own suites.)
"""
import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
VAULT_SRC = HERE.parent


@pytest.fixture
def vault(tmp_path):
    dst = tmp_path / "vault"
    shutil.copytree(VAULT_SRC, dst, ignore=shutil.ignore_patterns("tests", "__pycache__", ".git"))
    # build the index so lint's index-dependent checks have input
    subprocess.run([sys.executable, "-m", "agent_vault.build_index", "."],
                   cwd=str(dst), capture_output=True, check=True)
    return dst


def _run(vault, *args):
    return subprocess.run([sys.executable, "-m", *args], cwd=str(vault),
                          capture_output=True, text=True)


# ---------------------------------------------------------------------------
# lint
# ---------------------------------------------------------------------------

def test_lint_help_exits_zero(vault):
    out = _run(vault, "agent_vault.lint", "--help")
    assert out.returncode == 0
    assert "lint" in out.stdout.lower()


def test_lint_human_report_and_exit_code(vault):
    out = _run(vault, "agent_vault.lint", ".")
    # exit is 0 (clean) or 1 (issues found); either way it must report a total
    assert out.returncode in (0, 1)
    assert "total issues:" in out.stdout


def test_lint_json_output_is_valid(vault):
    out = _run(vault, "agent_vault.lint", ".", "--json")
    data = json.loads(out.stdout)
    assert "total_issues" in data
    assert (out.returncode == 1) == (data["total_issues"] > 0)


def test_lint_report_file_written(vault):
    out = _run(vault, "agent_vault.lint", ".", "--report", "lint_report.json")
    assert out.returncode in (0, 1)
    report = json.loads((vault / "lint_report.json").read_text(encoding="utf-8"))
    assert "total_issues" in report


# ---------------------------------------------------------------------------
# compact
# ---------------------------------------------------------------------------

def test_compact_dry_run(vault):
    # seed a discovery log with duplicate proposals so there's something to bound
    disc = vault / "discovery"
    disc.mkdir(exist_ok=True)
    dup = json.dumps({"kind": "tag", "name": "banking", "entity": "x"})
    (disc / "proposals.jsonl").write_text(dup + "\n" + dup + "\n", encoding="utf-8")

    out = _run(vault, "agent_vault.compact", ".")
    assert out.returncode == 0
    assert "dry-run" in out.stdout
    # dry run leaves the file untouched (no .bak)
    assert not (disc / "proposals.jsonl.bak").exists()


def test_compact_apply_writes_backup(vault):
    disc = vault / "discovery"
    disc.mkdir(exist_ok=True)
    dup = json.dumps({"kind": "tag", "name": "banking", "entity": "x"})
    (disc / "proposals.jsonl").write_text(dup + "\n" + dup + "\n", encoding="utf-8")

    out = _run(vault, "agent_vault.compact", ".", "--apply")
    assert out.returncode == 0
    assert "compacted" in out.stdout
    assert (disc / "proposals.jsonl.bak").exists()


# ---------------------------------------------------------------------------
# review
# ---------------------------------------------------------------------------

def test_review_help_exits_zero(vault):
    out = _run(vault, "agent_vault.review", "--help")
    assert out.returncode == 0


def test_review_no_command_exits_two(vault):
    out = _run(vault, "agent_vault.review", ".")
    assert out.returncode == 2


def test_review_list_and_entities(vault):
    lst = _run(vault, "agent_vault.review", ".", "list")
    assert lst.returncode == 0
    ents = _run(vault, "agent_vault.review", ".", "entities")
    assert ents.returncode == 0

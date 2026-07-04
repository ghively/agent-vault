"""build_index.py robustness: one malformed entity must be skipped with a
warning (not abort the whole build), and non-string frontmatter values (an
int title) must not crash the index or the _match normalization."""
import json
import shutil
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
VAULT_SRC = HERE.parent

LINKS = "<!-- LINKS:BEGIN -->\n<!-- LINKS:END -->\n"


def _sandbox(tmp_path):
    dst = tmp_path / "vault"
    shutil.copytree(VAULT_SRC, dst, ignore=shutil.ignore_patterns("tests", "__pycache__"))
    return dst


def _run(vault):
    return subprocess.run(
        [sys.executable, "-m", "agent_vault.build_index", "."],
        cwd=str(vault),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )


def test_malformed_yaml_entity_is_skipped_build_succeeds(tmp_path):
    vault = _sandbox(tmp_path)
    bad = vault / "entities" / "document" / "bad-yaml.md"
    bad.write_text("---\ntitle: [unclosed flow sequence\n---\n\n" + LINKS,
                   encoding="utf-8")
    out = _run(vault)
    assert out.returncode == 0, out.stdout + out.stderr
    assert "bad-yaml" in out.stderr  # warned, not silent
    idx = json.loads((vault / "_index.json").read_text(encoding="utf-8"))
    assert idx["count"] >= 1  # the rest of the vault still indexed
    assert all("bad-yaml" not in e["path"] for e in idx["entities"])


def test_int_title_does_not_crash(tmp_path):
    vault = _sandbox(tmp_path)
    ent = vault / "entities" / "document" / "int-title.md"
    ent.write_text(
        "---\n"
        "slug: int-title\n"
        "type: document\n"
        "subtype: receipt\n"
        "title: 1984\n"          # loads as int, not str
        "status: stub\n"
        "confidence: 0.9\n"
        "created: 2026-07-04\n"
        "sources: []\n"
        "sources_hash: abc\n"
        "---\n\n" + LINKS,
        encoding="utf-8",
    )
    out = _run(vault)
    assert out.returncode == 0, out.stdout + out.stderr
    idx = json.loads((vault / "_index.json").read_text(encoding="utf-8"))
    rec = next(e for e in idx["entities"] if e.get("slug") == "int-title")
    assert "1984" in rec["_match"]  # coerced to str for matching

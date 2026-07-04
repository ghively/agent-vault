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
        [sys.executable, "-m", "agent_vault.validate", "."],
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


# ---------------------------------------------------------------------------
# validate_patterns — the checks DOCS promises for registry/patterns.yaml
# ---------------------------------------------------------------------------

def test_validate_patterns_flags_bad_tags_types_and_duplicate_shape_ids(tmp_path):
    from agent_vault import validate

    vault = _sandbox(tmp_path)
    (vault / "registry" / "patterns.yaml").write_text(
        "version: 1\n"
        "billers:\n"
        "  - id: acme\n"
        "    matches: [\"acme\"]\n"
        "    default_tags: [banking, not-a-real-tag]\n"
        "    confidence: 0.9\n"
        "shapes:\n"
        "  - id: dup-shape\n"
        "    type: document\n"
        "    subtype: statement\n"
        "    keywords: [\"x\"]\n"
        "  - id: dup-shape\n"
        "    type: nosuchtype\n"
        "    subtype: whatever\n"
        "    keywords: [\"y\"]\n"
        "  - id: bad-subtype-shape\n"
        "    type: document\n"
        "    subtype: not-a-subtype\n"
        "    keywords: [\"z\"]\n",
        encoding="utf-8",
    )
    errs = validate.validate_patterns(str(vault))
    assert any("default_tag" in e and "not-a-real-tag" in e for e in errs)
    assert any("duplicate shape id" in e for e in errs)
    assert any("nosuchtype" in e for e in errs)
    assert any("not-a-subtype" in e for e in errs)


def test_validate_patterns_clean_on_shipped_registry():
    from agent_vault import validate

    assert validate.validate_patterns(str(VAULT_SRC)) == []


def test_validate_file_reports_empty_schema_type_body(tmp_path):
    from agent_vault import validate

    vault = _sandbox(tmp_path)
    edir = vault / "entities" / "emptytype"
    edir.mkdir()
    ent = edir / "thing.md"
    ent.write_text(
        "---\n"
        "slug: thing\n"
        "type: emptytype\n"
        "subtype: whatever\n"
        "title: Thing\n"
        "status: stub\n"
        "confidence: 0.9\n"
        "created: 2026-07-04\n"
        "sources: []\n"
        "sources_hash: abc\n"
        "---\n\n"
        "<!-- LINKS:BEGIN -->\n<!-- LINKS:END -->\n",
        encoding="utf-8",
    )
    # A type whose body is empty parses as None in schema.yaml.
    schema = {"types": {"emptytype": None}, "tags": {}}
    errs, _warns = validate.validate_file(str(ent), schema, str(vault))  # must not raise
    assert any("empty" in e for e in errs)


def test_validate_unknown_tag_message_is_a_clear_error(tmp_path):
    vault = _sandbox(tmp_path)
    target = next((vault / "entities").glob("*/*.md"))
    text = target.read_text(encoding="utf-8")
    corrupted = re.sub(r"(?m)^tags:.*$", "tags: [definitely-not-registered]",
                       text, count=1)
    assert corrupted != text, "expected a tags line to rewrite"
    target.write_text(corrupted, encoding="utf-8")
    out = _run(vault)
    assert out.returncode == 1
    assert "not in registry" in out.stdout
    assert "ok during seeding" not in out.stdout  # old contradictory wording

"""Tests for O2 — vault snapshot / export / restore."""

import shutil
import tarfile
from pathlib import Path

import pytest

from agent_vault import backup


def _vault(tmp_path: Path, name: str = "src") -> Path:
    vault = tmp_path / name
    (vault / "entities" / "asset").mkdir(parents=True)
    (vault / "discovery").mkdir(parents=True)
    shutil.copytree("registry", vault / "registry")
    (vault / "entities" / "asset" / "f.md").write_text(
        "---\nslug: f\ntype: asset\nsubtype: hvac\ntitle: F\nstatus: compiled\n"
        "sources_hash: h\ncompiled_from_hash: h\n---\n\n"
        "<!-- LINKS:BEGIN -->\n<!-- LINKS:END -->\n\nProse.\n", encoding="utf-8")
    (vault / "raw").mkdir()
    (vault / "raw" / "doc.txt").write_text("source doc", encoding="utf-8")
    (vault / "discovery" / "proposals.jsonl").write_text('{"x":1}\n', encoding="utf-8")
    # derived artifacts that must be excluded from the archive
    (vault / "_index.json").write_text("{}", encoding="utf-8")
    (vault / "_vectors.db").write_text("binary", encoding="utf-8")
    return vault


def test_export_excludes_derived_and_raw_by_default(tmp_path):
    vault = _vault(tmp_path)
    arc = str(tmp_path / "b.tar.gz")
    summary = backup.export_vault(vault=str(vault), out_path=arc)
    assert summary["counts"]["entities"] == 1
    assert "raw" not in summary["counts"]  # raw excluded by default
    with tarfile.open(arc) as t:
        names = t.getnames()
    assert "entities/asset/f.md" in names
    assert "registry/schema.yaml" in names
    assert "discovery/proposals.jsonl" in names
    assert "_index.json" not in names   # derived, excluded
    assert "_vectors.db" not in names
    assert not any(n.startswith("raw/") for n in names)
    assert backup.META_NAME in names


def test_export_include_raw(tmp_path):
    vault = _vault(tmp_path)
    arc = str(tmp_path / "full.tar.gz")
    summary = backup.export_vault(str(vault), arc, include_raw=True)
    assert summary["counts"].get("raw") == 1
    with tarfile.open(arc) as t:
        assert "raw/doc.txt" in t.getnames()


def test_restore_roundtrip_and_reindex(tmp_path):
    vault = _vault(tmp_path)
    arc = str(tmp_path / "b.tar.gz")
    backup.export_vault(str(vault), arc)
    dest = tmp_path / "restored"
    result = backup.restore_vault(arc, str(dest))
    assert result["members"] >= 3
    assert (dest / "entities" / "asset" / "f.md").read_text().strip().endswith("Prose.")
    assert (dest / "registry" / "schema.yaml").exists()
    # derived index rebuilt on restore
    assert (dest / "_index.json").exists()


def test_restore_refuses_nonempty_vault_without_force(tmp_path):
    vault = _vault(tmp_path)
    arc = str(tmp_path / "b.tar.gz")
    backup.export_vault(str(vault), arc)
    # restoring back over the (non-empty) source must refuse
    with pytest.raises(SystemExit):
        backup.restore_vault(arc, str(vault))
    # ...unless forced
    backup.restore_vault(arc, str(vault), force=True)


def test_restore_rejects_path_traversal(tmp_path):
    """A tampered archive with a ../ member must not escape the destination."""
    arc = tmp_path / "evil.tar.gz"
    with tarfile.open(arc, "w:gz") as t:
        payload = b"pwned"
        info = tarfile.TarInfo("../escape.txt")
        info.size = len(payload)
        import io
        t.addfile(info, io.BytesIO(payload))
    dest = tmp_path / "dest"
    dest.mkdir()
    backup.restore_vault(str(arc), str(dest))
    assert not (tmp_path / "escape.txt").exists()  # traversal blocked


def test_list_reads_metadata(tmp_path):
    vault = _vault(tmp_path)
    arc = str(tmp_path / "b.tar.gz")
    backup.export_vault(str(vault), arc, include_raw=True)
    meta = backup._read_meta(arc)
    assert meta["include_raw"] is True
    assert "created" in meta and "counts" in meta

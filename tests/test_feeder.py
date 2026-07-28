"""Tests for the watched-folder ingestion feeder (Phase 4)."""
import json
import os
import shutil
import sys
from pathlib import Path

import pytest

# Ensure the feeders package is importable.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def _make_vault(tmp_path: Path) -> Path:
    """Create a minimal vault with registry and raw dirs."""
    vault = tmp_path / "vault"
    (vault / "entities" / "document").mkdir(parents=True)
    (vault / "raw" / "documents").mkdir(parents=True)
    (vault / "raw" / "email").mkdir(parents=True)
    (vault / "raw" / "misc").mkdir(parents=True)
    (vault / "raw" / "media").mkdir(parents=True)
    (vault / "raw" / "statements").mkdir(parents=True)
    (vault / "discovery").mkdir(parents=True)
    (vault / "registry").mkdir(parents=True)
    (vault / "inbox").mkdir(parents=True)
    # Copy real registry for a working schema.
    shutil.copytree("registry", str(vault / "registry"), dirs_exist_ok=True)
    # Build initial index.
    from agent_vault import build_index
    old_argv = sys.argv
    sys.argv = ["build_index", str(vault)]
    try:
        build_index.main()
    finally:
        sys.argv = old_argv
    return vault


def _drop_file(vault: Path, name: str, content: bytes = b"") -> Path:
    """Drop a file into the inbox."""
    p = vault / "inbox" / name
    p.write_bytes(content)
    return p


def test_feeder_moves_file_to_raw(tmp_path):
    """Dropping a file in inbox moves it to raw/<category>/."""
    vault = _make_vault(tmp_path)
    _drop_file(vault, "test-doc.txt", b"some text content here")
    from agent_vault.feeders.watch import process_inbox
    result = process_inbox(str(vault), str(vault / "inbox"))
    assert "error" not in result
    assert len(result["moved"]) == 1
    fname, dest, sha = result["moved"][0]
    assert "test-doc.txt" in fname
    assert "raw/misc/" in dest
    # The file should no longer be in inbox — wait, process_inbox copies,
    # doesn't remove from inbox. The ingest manifest prevents reprocessing.
    # Check the dest file exists.
    assert (vault / dest).exists()


def test_feeder_category_mapping(tmp_path):
    """Files are routed to the correct raw/ subdirectory by extension."""
    vault = _make_vault(tmp_path)
    _drop_file(vault, "report.pdf", b"%PDF-1.4 test")
    _drop_file(vault, "photo.jpg", b"\xff\xd8\xff\xe0 test jpeg")
    _drop_file(vault, "note.txt", b"a note")
    from agent_vault.feeders.watch import process_inbox
    result = process_inbox(str(vault), str(vault / "inbox"))
    dests = [d for _, d, _ in result["moved"]]
    assert any("raw/documents/" in d for d in dests), "PDF should go to documents"
    assert any("raw/media/" in d for d in dests), "JPG should go to media"
    assert any("raw/misc/" in d for d in dests), "TXT should go to misc"


def test_feeder_dry_run_no_side_effects(tmp_path):
    """--dry-run reports what would happen without moving files."""
    vault = _make_vault(tmp_path)
    _drop_file(vault, "dry-test.txt", b"dry run content")
    from agent_vault.feeders.watch import process_inbox
    result = process_inbox(str(vault), str(vault / "inbox"), dry_run=True)
    assert len(result["moved"]) == 1
    # File should NOT be in raw/
    raw_files = list((vault / "raw").rglob("*"))
    raw_files = [f for f in raw_files if f.is_file() and not f.name.startswith("_")]
    assert len(raw_files) == 0, "dry-run should not copy files"


def test_feeder_empty_inbox(tmp_path):
    """Empty inbox returns gracefully."""
    vault = _make_vault(tmp_path)
    from agent_vault.feeders.watch import process_inbox
    result = process_inbox(str(vault), str(vault / "inbox"))
    assert result["moved"] == []
    assert result["skipped"] == []


def test_feeder_timestamped_name(tmp_path):
    """Files get timestamped names to avoid collisions."""
    vault = _make_vault(tmp_path)
    _drop_file(vault, "manual.pdf", b"%PDF-1.4 test content")
    from agent_vault.feeders.watch import process_inbox
    result = process_inbox(str(vault), str(vault / "inbox"))
    _, dest, _ = result["moved"][0]
    # Should contain a timestamp pattern like 20260728-XXXXXX-manual.pdf
    import re
    basename = Path(dest).name
    assert re.match(r"\d{8}-\d{6}-manual\.pdf", basename), \
        f"expected timestamped name, got {basename}"


def test_feeder_skips_duplicate_by_hash(tmp_path):
    """A file already in the vault (by hash) is skipped."""
    vault = _make_vault(tmp_path)
    content = b"unique duplicate test content"
    # First, place a file in raw/ and add its hash to the manifest.
    raw_file = vault / "raw" / "misc" / "existing.txt"
    raw_file.write_bytes(content)
    import hashlib
    sha = hashlib.sha256(content).hexdigest()
    manifest = vault / "raw" / "_manifest.jsonl"
    manifest.write_text(json.dumps({"hash": sha, "path": "raw/misc/existing.txt"}) + "\n")
    # Drop the same content in inbox.
    _drop_file(vault, "duplicate.txt", content)
    from agent_vault.feeders.watch import process_inbox
    result = process_inbox(str(vault), str(vault / "inbox"))
    assert len(result["skipped"]) == 1
    assert "already ingested" in result["skipped"][0][1]


def test_feeder_no_inbox_dir(tmp_path):
    """Missing inbox directory is a no-op (nothing to do), not an error.

    A missing inbox is the normal steady state between file drops — cadences
    must not fail when no files have been deposited yet."""
    vault = _make_vault(tmp_path)
    from agent_vault.feeders.watch import process_inbox
    result = process_inbox(str(vault), str(vault / "nonexistent"))
    assert result["moved"] == []
    assert result["skipped"] == []
    assert result["ingest"] is None

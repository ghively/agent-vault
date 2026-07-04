"""Coverage for collections_importer.py — Stage 6 catalog-media import.

This module had the worst coverage ratio in the repo (612 lines, only
_yaml_quote exercised). The suite covers format detection, the per-format row
normalizers, the CSV/JSON readers, the entity writer (including slug-collision
disambiguation and identifier-based idempotency), the import driver
(dry-run / bad-row / poison-file isolation), and main().

A sandbox copy of the repo supplies the real registry/schema.yaml (the importer
validates each row's subtype against collection's subtypes).
"""
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from agent_vault import collections_importer as ci

HERE = Path(__file__).resolve().parent
VAULT_SRC = HERE.parent


def _sandbox(tmp_path):
    dst = tmp_path / "vault"
    shutil.copytree(VAULT_SRC, dst, ignore=shutil.ignore_patterns("tests", "__pycache__", ".git"))
    return dst


def _write_csv(path, headers, rows):
    lines = [",".join(headers)]
    for r in rows:
        lines.append(",".join(str(c) for c in r))
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


# ---------------------------------------------------------------------------
# format detection + pure helpers
# ---------------------------------------------------------------------------

def test_detect_format():
    assert ci.detect_format(["AppID", "Name"]) == "steam"
    assert ci.detect_format(["Title", "ISBN13", "Author"]) == "goodreads"
    assert ci.detect_format(["Title", "Title Type", "Year"]) == "imdb"
    assert ci.detect_format(["Name", "Letterboxd URI"]) == "letterboxd"
    assert ci.detect_format(["Title", "release_id", "Artist"]) == "discogs"
    assert ci.detect_format(["Title", "Narrator", "ASIN"]) == "audible"
    assert ci.detect_format(["Title", "ASIN"]) == "kindle"
    assert ci.detect_format(["title", "subtype"]) == "generic"
    assert ci.detect_format(["foo", "bar"]) == "unknown"


def test_slugify_and_to_num():
    assert ci._slugify("The Legend of Zelda!") == "the-legend-of-zelda"
    assert ci._slugify("  ") == "untitled"
    assert ci._to_num("42") == 42
    assert ci._to_num("3.5") == 3.5
    assert ci._to_num("") is None
    assert ci._to_num("not-a-number") is None


def test_yaml_quote_retyping_guard():
    # a bare "1984" would load back as an int; the writer must quote it
    assert ci._yaml_quote("1984") == '"1984"'
    assert ci._yaml_quote("no") == '"no"'
    assert ci._yaml_quote("plain title") == "plain title"


def test_normalizers():
    steam = ci.from_steam({"Name": "Portal 2", "AppID": "620",
                           "hours_on_record": "12.0"})
    assert steam["subtype"] == "game" and steam["title"] == "Portal 2"
    assert steam["identifier"] == "steam:620" and steam["hours_played"] == 12

    book = ci.from_goodreads({"Title": "Dune", "Author": "Herbert",
                              "ISBN13": "9780441172719", "My Rating": "5"})
    assert book["subtype"] == "book" and book["identifier"] == "isbn:9780441172719"
    assert book["rating"] == 5

    tv = ci.from_imdb({"Title": "The Wire", "Title Type": "tvSeries", "Const": "tt0306414"})
    assert tv["subtype"] == "tv" and tv["identifier"] == "imdb:tt0306414"

    assert ci.from_generic({"title": "X"}) is None          # missing subtype
    assert ci.from_steam({"AppID": "1"}) is None             # missing title


# ---------------------------------------------------------------------------
# readers
# ---------------------------------------------------------------------------

def test_read_rows_csv_and_json(tmp_path):
    csvp = tmp_path / "s.csv"
    _write_csv(csvp, ["title", "subtype"], [["A", "game"]])
    headers, rows = ci.read_rows(str(csvp))
    assert headers == ["title", "subtype"] and rows[0]["title"] == "A"

    jsonp = tmp_path / "s.json"
    jsonp.write_text(json.dumps({"items": [{"title": "B", "subtype": "book"}]}), encoding="utf-8")
    headers, rows = ci.read_rows(str(jsonp))
    assert "title" in headers and rows[0]["subtype"] == "book"

    with pytest.raises(ValueError, match="unsupported extension"):
        ci.read_rows(str(tmp_path / "s.txt"))


# ---------------------------------------------------------------------------
# import pipeline: write, idempotency, bad rows, dry-run
# ---------------------------------------------------------------------------

def test_import_steam_csv_writes_and_is_idempotent(tmp_path):
    vault = _sandbox(tmp_path)
    src = vault / "raw" / "collections" / "steam.csv"
    src.parent.mkdir(parents=True, exist_ok=True)
    _write_csv(src, ["Name", "AppID"], [["Half-Life", "70"], ["Portal", "400"]])

    rec = ci.import_file(str(vault), str(src))
    assert rec["format"] == "steam" and rec["imported"] == 2
    assert (vault / "entities" / "collection" / "half-life.md").exists()

    # second run: same rows -> all skipped, no duplicates
    rec2 = ci.import_file(str(vault), str(src))
    assert rec2["imported"] == 0 and rec2["skipped"] == 2


def test_import_generic_invalid_subtype_is_bad_row(tmp_path):
    vault = _sandbox(tmp_path)
    src = vault / "raw" / "collections" / "g.csv"
    src.parent.mkdir(parents=True, exist_ok=True)
    _write_csv(src, ["title", "subtype"], [["Good", "game"], ["Bad", "spaceship"]])
    rec = ci.import_file(str(vault), str(src))
    assert rec["imported"] == 1
    assert len(rec["bad"]) == 1 and "spaceship" in rec["bad"][0]["error"]


def test_dry_run_writes_nothing(tmp_path):
    vault = _sandbox(tmp_path)
    src = vault / "raw" / "collections" / "steam.csv"
    src.parent.mkdir(parents=True, exist_ok=True)
    _write_csv(src, ["Name", "AppID"], [["DryGame", "1"]])
    rec = ci.import_file(str(vault), str(src), dry_run=True)
    assert rec["imported"] == 1
    assert not (vault / "entities" / "collection" / "drygame.md").exists()
    assert not (vault / ci.IMPORTS_LOG).exists()


def test_unknown_format_is_reported(tmp_path):
    vault = _sandbox(tmp_path)
    src = vault / "raw" / "collections" / "mystery.csv"
    src.parent.mkdir(parents=True, exist_ok=True)
    _write_csv(src, ["foo", "bar"], [["1", "2"]])
    rec = ci.import_file(str(vault), str(src))
    assert rec["format"] == "unknown" and rec["imported"] == 0


# ---------------------------------------------------------------------------
# write_entity disambiguation + _same_logical_entity
# ---------------------------------------------------------------------------

def test_slug_collision_disambiguates_by_year(tmp_path):
    vault = _sandbox(tmp_path)
    today = "2026-07-04"
    a = {"_slug_base": "the-thing", "subtype": "film", "title": "The Thing",
         "identifier": "imdb:tt1", "year": 1982, "platform": "imdb"}
    b = {"_slug_base": "the-thing", "subtype": "film", "title": "The Thing",
         "identifier": "imdb:tt2", "year": 2011, "platform": "imdb"}
    p1, c1 = ci.write_entity(str(vault), a, str(vault / "raw/x.csv"), today)
    p2, c2 = ci.write_entity(str(vault), b, str(vault / "raw/x.csv"), today)
    assert c1 and c2
    assert os.path.basename(p1) == "the-thing.md"
    assert os.path.basename(p2) == "the-thing-2011.md"      # disambiguated by year


def test_same_logical_entity_by_identifier_and_title(tmp_path):
    vault = _sandbox(tmp_path)
    today = "2026-07-04"
    ent = {"_slug_base": "dune", "subtype": "book", "title": "Dune",
           "identifier": "isbn:9780441172719"}
    path, created = ci.write_entity(str(vault), ent, str(vault / "raw/x.csv"), today)
    assert created
    # same identifier -> logical match (idempotent)
    assert ci._same_logical_entity(path, ent) is True
    # different identifier -> not a match
    other = {**ent, "identifier": "isbn:0000000000000"}
    assert ci._same_logical_entity(path, other) is False


# ---------------------------------------------------------------------------
# import_dir poison-file isolation
# ---------------------------------------------------------------------------

def test_import_dir_isolates_poison_file(tmp_path):
    vault = _sandbox(tmp_path)
    d = vault / "raw" / "collections"
    d.mkdir(parents=True, exist_ok=True)
    _write_csv(d / "good.csv", ["Name", "AppID"], [["G", "1"]])
    (d / "bad.json").write_text("{ this is not valid json", encoding="utf-8")
    (d / "_ignored.csv").write_text("title,subtype\nX,game\n", encoding="utf-8")  # underscore -> skipped

    results = ci.import_dir(str(vault), str(d))
    fmts = {os.path.basename(r["path"]): r["format"] for r in results}
    assert fmts.get("good.csv") == "steam"
    assert fmts.get("bad.json") == "error"          # isolated, not crashed
    assert "_ignored.csv" not in fmts                # underscore-prefixed skipped


# ---------------------------------------------------------------------------
# main() CLI
# ---------------------------------------------------------------------------

def test_main_cli_dry_run(tmp_path):
    vault = _sandbox(tmp_path)
    src = vault / "raw" / "collections" / "steam.csv"
    src.parent.mkdir(parents=True, exist_ok=True)
    _write_csv(src, ["Name", "AppID"], [["CLIGame", "9"]])
    out = subprocess.run(
        [sys.executable, "-m", "agent_vault.collections_importer", ".",
         "--source", "raw/collections/steam.csv", "--dry-run"],
        cwd=str(vault), capture_output=True, text=True)
    assert out.returncode == 0, out.stderr
    assert "dry run" in out.stdout and "1 new" in out.stdout
    assert not (vault / "entities" / "collection" / "cligame.md").exists()

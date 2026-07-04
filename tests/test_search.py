"""Tests for agent_vault.search — the FTS5 full-text retrieval layer (O4.1).

Covers prose extraction, the FTS5 build + ranked query, the metadata fallback
when no sidecar exists, and MATCH-expression sanitization.
"""

import json
from pathlib import Path

import pytest

from agent_vault import search


def _write_entity(vault: Path, typ: str, slug: str, title: str, prose: str,
                  tags=None, status: str = "compiled") -> None:
    d = vault / "entities" / typ
    d.mkdir(parents=True, exist_ok=True)
    tags_line = f"tags: [{', '.join(tags)}]\n" if tags else ""
    (d / f"{slug}.md").write_text(
        f"---\n"
        f"slug: {slug}\n"
        f"type: {typ}\n"
        f"subtype: general\n"
        f"title: {title}\n"
        f"status: {status}\n"
        f"{tags_line}"
        f"---\n\n"
        f"<!-- LINKS:BEGIN -->\n- Related: [[x/y]]\n<!-- LINKS:END -->\n\n"
        f"{prose}\n",
        encoding="utf-8",
    )


def _make_vault(tmp_path: Path) -> Path:
    vault = tmp_path / "vault"
    vault.mkdir()
    _write_entity(vault, "asset", "carrier-furnace", "Carrier Furnace",
                  "The gas furnace serves the whole-house forced-air heating system.",
                  tags=["heating"])
    _write_entity(vault, "document", "furnace-warranty", "Furnace Warranty",
                  "Manufacturer parts warranty covering the furnace for ten years.")
    _write_entity(vault, "account", "checking", "Checking Account",
                  "Primary household checking account used for direct deposit.")
    return vault


# --- prose extraction -------------------------------------------------------

def test_prose_of_after_links_block():
    raw = ("---\nslug: x\n---\n\n"
           "<!-- LINKS:BEGIN -->\n- Related: [[a/b]]\n<!-- LINKS:END -->\n\n"
           "This is the real prose body.")
    assert search.prose_of(raw) == "This is the real prose body."


def test_prose_of_without_links_block_falls_back_to_body():
    raw = "---\nslug: x\n---\n\nProse with no links block."
    assert search.prose_of(raw) == "Prose with no links block."


# --- FTS5 build + query -----------------------------------------------------

@pytest.mark.skipif(not search.fts5_available(), reason="SQLite built without FTS5")
def test_build_search_index_counts_all_entities(tmp_path):
    vault = _make_vault(tmp_path)
    n = search.build_search_index(str(vault))
    assert n == 3
    assert (vault / search.DB_NAME).exists()
    assert not (vault / (search.DB_NAME + ".tmp")).exists()  # temp cleaned up


@pytest.mark.skipif(not search.fts5_available(), reason="SQLite built without FTS5")
def test_search_finds_prose_only_term(tmp_path):
    """The headline win: a phrase that appears ONLY in prose (never in metadata)
    is now findable — the old substring-over-metadata scan could not do this."""
    vault = _make_vault(tmp_path)
    search.build_search_index(str(vault))
    hits = search.search(str(vault), "forced-air heating")
    slugs = [h["slug"] for h in hits]
    assert "carrier-furnace" in slugs
    top = hits[0]
    assert top["snippet"]  # FTS path returns a prose snippet
    assert "score" in top


@pytest.mark.skipif(not search.fts5_available(), reason="SQLite built without FTS5")
def test_search_ranks_by_relevance(tmp_path):
    vault = _make_vault(tmp_path)
    search.build_search_index(str(vault))
    hits = search.search(str(vault), "furnace warranty")
    slugs = [h["slug"] for h in hits]
    # Both furnace docs match; the warranty entity mentions both terms.
    assert "furnace-warranty" in slugs
    assert "checking" not in slugs


@pytest.mark.skipif(not search.fts5_available(), reason="SQLite built without FTS5")
def test_search_empty_query_returns_empty(tmp_path):
    vault = _make_vault(tmp_path)
    search.build_search_index(str(vault))
    assert search.search(str(vault), "   ") == []
    assert search.search(str(vault), "!!!") == []  # no alphanumeric tokens


@pytest.mark.skipif(not search.fts5_available(), reason="SQLite built without FTS5")
def test_search_match_expr_is_injection_safe(tmp_path):
    vault = _make_vault(tmp_path)
    search.build_search_index(str(vault))
    # Special characters that would break a raw FTS5 MATCH must not raise.
    for q in ['furnace"OR', "a AND b", 'drop; --', "((", "*"]:
        assert isinstance(search.search(str(vault), q), list)


def test_match_expr_tokenizes_and_prefixes():
    assert search._match_expr("Furnace, Warranty!") == "furnace* OR warranty*"
    assert search._match_expr("") == ""
    assert search._match_expr("...") == ""


# --- fallback path (no FTS db) ---------------------------------------------

def test_fallback_used_when_no_db(tmp_path):
    """With no _index.db, search falls back to a metadata token-overlap scan
    over _index.json so callers still get ranked results."""
    vault = _make_vault(tmp_path)
    # Build only the JSON index, not the FTS db.
    index = {
        "entities": [
            {"slug": "carrier-furnace", "title": "Carrier Furnace", "type": "asset",
             "subtype": "general", "status": "compiled", "path": "entities/asset/carrier-furnace.md",
             "tags": ["heating"], "_match": ["carrier-furnace", "carrier furnace"]},
        ]
    }
    (vault / "_index.json").write_text(json.dumps(index), encoding="utf-8")
    assert not (vault / search.DB_NAME).exists()

    hits = search.search(str(vault), "furnace")
    assert hits and hits[0]["slug"] == "carrier-furnace"
    assert hits[0]["snippet"] == ""  # fallback can't see prose


def test_fallback_empty_when_nothing_indexed(tmp_path):
    vault = tmp_path / "empty"
    vault.mkdir()
    assert search.search(str(vault), "anything") == []

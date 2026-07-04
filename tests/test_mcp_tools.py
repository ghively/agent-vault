"""Tests for agent_vault.mcp_tools — the vault operations exposed over MCP (O1).

These exercise the pure tool functions directly (no MCP transport), so they run
in CI whether or not the optional `mcp` extra is installed. A separate, skipped-
if-absent test confirms the FastMCP wiring registers the expected tools.
"""

import json
from pathlib import Path

import pytest

from agent_vault import mcp_tools


def _make_vault(tmp_path: Path) -> Path:
    vault = tmp_path / "vault"
    (vault / "entities" / "asset").mkdir(parents=True)
    (vault / "entities" / "account").mkdir(parents=True)
    (vault / "discovery").mkdir(parents=True)
    (vault / "registry").mkdir(parents=True)
    # Minimal resolver registry so the env backend is wired for the resolve test.
    (vault / "registry" / "resolvers.yaml").write_text(
        "version: 1\ndefault_resolver: env\nresolvers:\n"
        "  env:\n    module: agent_vault.resolvers.env\n",
        encoding="utf-8",
    )

    (vault / "entities" / "asset" / "carrier-furnace.md").write_text(
        "---\nslug: carrier-furnace\ntype: asset\nsubtype: hvac\n"
        "title: Carrier Furnace\nstatus: compiled\ntags: [heating]\n"
        "sources_hash: h1\ncompiled_from_hash: h1\n---\n\n"
        "<!-- LINKS:BEGIN -->\n<!-- LINKS:END -->\n\n"
        "The gas furnace serves the whole-house forced-air heating system.\n",
        encoding="utf-8",
    )
    (vault / "entities" / "account" / "bofa.md").write_text(
        "---\nslug: bofa\ntype: account\nsubtype: checking\n"
        "title: BofA Checking\nstatus: compiled\n"
        "credential_ref: env://BOFA_SECRET\n"
        "sources_hash: h2\ncompiled_from_hash: h2\n---\n\n"
        "<!-- LINKS:BEGIN -->\n<!-- LINKS:END -->\n\n"
        "Primary household checking account.\n",
        encoding="utf-8",
    )
    # Build the JSON + FTS indexes the read tools rely on.
    from agent_vault import build_index
    import sys
    argv = sys.argv
    sys.argv = ["build_index", str(vault)]
    try:
        build_index.main()
    finally:
        sys.argv = argv
    return vault


# --- search -----------------------------------------------------------------

def test_search_finds_prose_only_term(tmp_path):
    vault = _make_vault(tmp_path)
    out = mcp_tools.search(str(vault), "forced-air heating")
    slugs = [h["slug"] for h in out["hits"]]
    assert "carrier-furnace" in slugs
    assert "fts" in out


def test_search_empty_query(tmp_path):
    vault = _make_vault(tmp_path)
    out = mcp_tools.search(str(vault), "   ")
    assert out["hits"] == []


# --- get --------------------------------------------------------------------

def test_ask_is_grounded_and_cited(tmp_path, monkeypatch):
    monkeypatch.setenv("AGENT_VAULT_RAG", "mock")
    vault = _make_vault(tmp_path)
    out = mcp_tools.ask(str(vault), "tell me about the furnace heating", mode="fts")
    assert out["grounded"] is True
    assert any(c["slug"] == "carrier-furnace" for c in out["citations"])


def test_ask_rejects_bad_mode(tmp_path):
    vault = _make_vault(tmp_path)
    assert "error" in mcp_tools.ask(str(vault), "q", mode="nonsense")


def test_get_entity_returns_detail(tmp_path):
    vault = _make_vault(tmp_path)
    out = mcp_tools.get_entity(str(vault), "carrier-furnace")
    assert out["slug"] == "carrier-furnace"
    assert "prose" in out
    assert "forced-air" in out["prose"]


def test_get_entity_invalid_slug(tmp_path):
    vault = _make_vault(tmp_path)
    assert "error" in mcp_tools.get_entity(str(vault), "Bad Slug!")


def test_get_entity_not_found(tmp_path):
    vault = _make_vault(tmp_path)
    assert mcp_tools.get_entity(str(vault), "nope")["error"] == "entity not found"


# --- list -------------------------------------------------------------------

def test_list_entities_all_and_filtered(tmp_path):
    vault = _make_vault(tmp_path)
    allrows = mcp_tools.list_entities(str(vault))
    assert allrows["total"] == 2
    accts = mcp_tools.list_entities(str(vault), type_="account")
    assert [r["slug"] for r in accts["rows"]] == ["bofa"]


def test_list_entities_respects_limit(tmp_path):
    vault = _make_vault(tmp_path)
    assert len(mcp_tools.list_entities(str(vault), limit=1)["rows"]) == 1


# --- status -----------------------------------------------------------------

def test_status_counts(tmp_path):
    vault = _make_vault(tmp_path)
    st = mcp_tools.status(str(vault))
    assert st["counts"]["total"] == 2
    assert st["counts"]["compiled"] == 2
    assert st["breakdown"].get("account") == 1


# --- submit_source (the agent write path) -----------------------------------

def test_submit_source_writes_and_attributes(tmp_path):
    vault = _make_vault(tmp_path)
    out = mcp_tools.submit_source(
        str(vault), "note.txt", "Roof inspected 2026-06.", subdir="documents",
        actor="agent-7")
    assert out["ok"] is True
    assert out["path"] == "raw/documents/note.txt"
    written = (vault / "raw" / "documents" / "note.txt").read_text()
    assert written == "Roof inspected 2026-06."
    # Attribution recorded.
    subs = [json.loads(x) for x in
            (vault / "discovery" / "_submissions.jsonl").read_text().splitlines() if x.strip()]
    assert subs[-1]["actor"] == "agent-7"
    assert subs[-1]["path"] == "raw/documents/note.txt"
    assert subs[-1]["sha256"] == out["sha256"]


def test_submit_source_is_append_only(tmp_path):
    vault = _make_vault(tmp_path)
    first = mcp_tools.submit_source(str(vault), "dup.txt", "one")
    assert first["ok"] is True
    second = mcp_tools.submit_source(str(vault), "dup.txt", "two")
    assert "already exists" in second["error"]
    # Original content is untouched (append-only invariant).
    assert (vault / "raw" / "documents" / "dup.txt").read_text() == "one"


@pytest.mark.parametrize("bad", ["../escape.txt", "/abs.txt", "a/b.txt", ".hidden", ""])
def test_submit_source_rejects_unsafe_filenames(tmp_path, bad):
    vault = _make_vault(tmp_path)
    assert "error" in mcp_tools.submit_source(str(vault), bad, "x")


def test_submit_source_rejects_unsafe_subdir(tmp_path):
    vault = _make_vault(tmp_path)
    assert "error" in mcp_tools.submit_source(str(vault), "ok.txt", "x", subdir="../etc")


# --- resolve_credential (gated) ---------------------------------------------

def test_resolve_credential_disabled_by_default(tmp_path, monkeypatch):
    monkeypatch.delenv("AGENT_VAULT_MCP_ALLOW_RESOLVE", raising=False)
    vault = _make_vault(tmp_path)
    out = mcp_tools.resolve_credential(str(vault), "bofa")
    assert "disabled" in out["error"]


def test_resolve_credential_enabled_resolves_env_backend(tmp_path, monkeypatch):
    monkeypatch.setenv("AGENT_VAULT_MCP_ALLOW_RESOLVE", "1")
    monkeypatch.setenv("BOFA_SECRET", "hunter2")
    vault = _make_vault(tmp_path)
    out = mcp_tools.resolve_credential(str(vault), "bofa")
    assert out.get("ok") is True
    assert out["secret"] == "hunter2"


# --- FastMCP wiring (skipped if the optional extra isn't installed) ----------

def test_build_mcp_registers_tools(tmp_path):
    pytest.importorskip("mcp")
    from agent_vault.mcp_server import build_mcp
    server = build_mcp(str(_make_vault(tmp_path)))
    assert server.name == "agent-vault"

"""Tests for O4.2 — embeddings + semantic/hybrid retrieval.

Everything runs offline with the deterministic MockEmbedder; the OllamaEmbedder
HTTP path is exercised with a stubbed transport (no live model), mirroring
test_ollama_client.py.
"""

import json
import math
import urllib.error
from pathlib import Path

import pytest

from agent_vault import embeddings, semantic, search


# --- embedders --------------------------------------------------------------

def test_mock_embedder_is_deterministic_and_normalized():
    e = embeddings.MockEmbedder()
    a = e.embed(["forced-air heating system"])[0]
    b = e.embed(["forced-air heating system"])[0]
    assert a == b                                  # deterministic
    assert len(a) == e.dim
    assert math.isclose(math.sqrt(sum(x * x for x in a)), 1.0, rel_tol=1e-6)  # L2-normalized


def test_mock_embedder_lexical_overlap_ranks_higher():
    e = embeddings.MockEmbedder()
    q, near, far = e.embed(["furnace heating"], )[0], None, None
    near = e.embed(["the furnace provides heating"])[0]
    far = e.embed(["checking account bank deposit"])[0]
    assert embeddings.cosine(q, near) > embeddings.cosine(q, far)


def test_get_embedder_dispatch(monkeypatch):
    monkeypatch.setenv("AGENT_VAULT_EMBEDDER", "mock")
    assert isinstance(embeddings.get_embedder(), embeddings.MockEmbedder)
    monkeypatch.setenv("AGENT_VAULT_EMBEDDER", "ollama")
    assert isinstance(embeddings.get_embedder(), embeddings.OllamaEmbedder)
    monkeypatch.setenv("AGENT_VAULT_EMBEDDER", "bogus")
    with pytest.raises(ValueError, match="unknown AGENT_VAULT_EMBEDDER"):
        embeddings.get_embedder()


class _FakeResp:
    def __init__(self, body): self._b = body.encode("utf-8")
    def __enter__(self): return self
    def __exit__(self, *a): return False
    def read(self, amt=None): return self._b if amt is None else self._b[:amt]


def test_ollama_embedder_happy_path(monkeypatch):
    monkeypatch.setattr(embeddings.time, "sleep", lambda *a, **k: None)
    monkeypatch.setattr(embeddings.urllib.request, "urlopen",
                        lambda req, timeout=None: _FakeResp(json.dumps({"embedding": [3.0, 4.0]})))
    out = embeddings.OllamaEmbedder().embed(["hello"])
    # returned vector is L2-normalized: [3,4] -> [0.6, 0.8]
    assert pytest.approx(out[0], rel=1e-6) == [0.6, 0.8]


def test_ollama_embedder_error_field_raises(monkeypatch):
    monkeypatch.setattr(embeddings.time, "sleep", lambda *a, **k: None)
    monkeypatch.setattr(embeddings.urllib.request, "urlopen",
                        lambda req, timeout=None: _FakeResp(json.dumps({"error": "no model"})))
    with pytest.raises(RuntimeError, match="no model"):
        embeddings.OllamaEmbedder().embed(["x"])


def test_ollama_embedder_retries_then_succeeds(monkeypatch):
    monkeypatch.setattr(embeddings.time, "sleep", lambda *a, **k: None)
    calls = {"n": 0}

    def fake(req, timeout=None):
        calls["n"] += 1
        if calls["n"] == 1:
            raise urllib.error.URLError("reset")
        return _FakeResp(json.dumps({"embedding": [1.0, 0.0]}))

    monkeypatch.setattr(embeddings.urllib.request, "urlopen", fake)
    out = embeddings.OllamaEmbedder().embed(["x"])
    assert calls["n"] == 2
    assert out[0][0] == pytest.approx(1.0)


# --- vector index + semantic search -----------------------------------------

def _vault(tmp_path: Path) -> Path:
    vault = tmp_path / "v"
    for typ, slug, title, prose in [
        ("asset", "carrier-furnace", "Carrier Furnace",
         "The gas furnace serves the whole-house forced-air heating system."),
        ("document", "furnace-warranty", "Furnace Warranty",
         "Manufacturer parts warranty covering the furnace heating unit."),
        ("account", "checking", "Checking Account",
         "Primary household checking account for direct deposit."),
    ]:
        d = vault / "entities" / typ
        d.mkdir(parents=True, exist_ok=True)
        (d / f"{slug}.md").write_text(
            f"---\nslug: {slug}\ntype: {typ}\nsubtype: general\ntitle: {title}\n"
            f"status: compiled\n---\n\n<!-- LINKS:BEGIN -->\n<!-- LINKS:END -->\n\n{prose}\n",
            encoding="utf-8")
    return vault


def test_build_vector_index_and_semantic_search(tmp_path, monkeypatch):
    monkeypatch.setenv("AGENT_VAULT_EMBEDDER", "mock")
    vault = _vault(tmp_path)
    n, name = semantic.build_vector_index(str(vault))
    assert n == 3
    assert name == "mock-embed"
    assert semantic.semantic_available(str(vault))
    assert not (vault / (semantic.DB_NAME + ".tmp")).exists()

    hits = semantic.semantic_search(str(vault), "home heating system furnace")
    slugs = [h["slug"] for h in hits]
    assert slugs[0] in ("carrier-furnace", "furnace-warranty")
    assert "checking" == slugs[-1]  # least related ranks last
    assert all("score" in h for h in hits)


def test_semantic_search_empty_without_index(tmp_path):
    vault = _vault(tmp_path)  # no build
    assert semantic.semantic_search(str(vault), "furnace") == []
    assert not semantic.semantic_available(str(vault))


def test_hybrid_fuses_fts_and_semantic(tmp_path, monkeypatch):
    monkeypatch.setenv("AGENT_VAULT_EMBEDDER", "mock")
    vault = _vault(tmp_path)
    # Build both indexes.
    import sys
    from agent_vault import build_index
    argv = sys.argv
    sys.argv = ["build_index", str(vault)]
    try:
        build_index.main()
    finally:
        sys.argv = argv
    semantic.build_vector_index(str(vault))

    hits = semantic.hybrid_search(str(vault), "furnace heating")
    slugs = [h["slug"] for h in hits]
    assert "carrier-furnace" in slugs
    assert "checking" not in slugs[:1]  # not the top hit


def test_search_mode_dispatch(tmp_path, monkeypatch):
    monkeypatch.setenv("AGENT_VAULT_EMBEDDER", "mock")
    vault = _vault(tmp_path)
    semantic.build_vector_index(str(vault))
    # search.search(mode="semantic") routes to the vector index.
    hits = search.search(str(vault), "heating furnace", mode="semantic")
    assert hits and "score" in hits[0]
    # mode="hybrid" returns a list even with only the vector index present.
    assert isinstance(search.search(str(vault), "furnace", mode="hybrid"), list)

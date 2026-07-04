"""Tests for O4.3 — grounded, cited RAG answers.

Offline throughout: retrieval uses the real FTS index, generation uses the
deterministic MockAnswerer (or a tiny custom Answerer), and the OllamaAnswerer
HTTP path is stubbed. No live model.
"""

import json
import sys
from pathlib import Path

import pytest

from agent_vault import rag, build_index


def _vault(tmp_path: Path) -> Path:
    vault = tmp_path / "v"
    for typ, slug, title, prose in [
        ("asset", "carrier-furnace", "Carrier Furnace",
         "The gas furnace serves the whole-house forced-air heating system, "
         "installed 2021, 10-year parts warranty."),
        ("account", "checking", "Checking Account",
         "Primary household checking account for direct deposit."),
    ]:
        d = vault / "entities" / typ
        d.mkdir(parents=True, exist_ok=True)
        (d / f"{slug}.md").write_text(
            f"---\nslug: {slug}\ntype: {typ}\nsubtype: general\ntitle: {title}\n"
            f"status: compiled\n---\n\n<!-- LINKS:BEGIN -->\n<!-- LINKS:END -->\n\n{prose}\n",
            encoding="utf-8")
    argv = sys.argv
    sys.argv = ["build_index", str(vault)]
    try:
        build_index.main()
    finally:
        sys.argv = argv
    return vault


# --- answerer dispatch ------------------------------------------------------

def test_get_answerer_dispatch(monkeypatch):
    monkeypatch.setenv("AGENT_VAULT_RAG", "mock")
    assert isinstance(rag.get_answerer(), rag.MockAnswerer)
    monkeypatch.setenv("AGENT_VAULT_RAG", "ollama")
    assert isinstance(rag.get_answerer(), rag.OllamaAnswerer)
    monkeypatch.setenv("AGENT_VAULT_RAG", "bogus")
    with pytest.raises(ValueError, match="unknown AGENT_VAULT_RAG"):
        rag.get_answerer()


# --- answer(): grounding + citations ----------------------------------------

def test_answer_is_grounded_and_cited(tmp_path, monkeypatch):
    monkeypatch.setenv("AGENT_VAULT_RAG", "mock")
    vault = _vault(tmp_path)
    out = rag.answer(str(vault), "tell me about the furnace heating", mode="fts")
    assert out["grounded"] is True
    cited = [c["slug"] for c in out["citations"]]
    assert "carrier-furnace" in cited
    assert out["sources"]  # retrieved contexts recorded
    assert out["client"] == "mock-rag"


def test_answer_empty_question(tmp_path):
    vault = _vault(tmp_path)
    out = rag.answer(str(vault), "   ", answerer=rag.MockAnswerer())
    assert out["answer"] == ""
    assert out["grounded"] is False


def test_answer_no_results_is_not_grounded(tmp_path):
    vault = _vault(tmp_path)
    out = rag.answer(str(vault), "zzznonexistentquery", mode="fts",
                     answerer=rag.MockAnswerer())
    # Nothing retrieved -> mock declines, nothing cited.
    assert out["grounded"] is False
    assert out["citations"] == []


def test_answer_drops_citations_outside_retrieved_context(tmp_path):
    """A model citing an entity that was NOT retrieved must not have that
    citation reported — grounding is verified against the actual context."""
    vault = _vault(tmp_path)

    class HallucinatingAnswerer(rag.Answerer):
        name = "halluc"

        def generate(self, question, contexts):
            # cite one real retrieved slug and one that wasn't provided
            return "See [[carrier-furnace]] and also [[made-up-entity]]."

    out = rag.answer(str(vault), "furnace", mode="fts",
                     answerer=HallucinatingAnswerer())
    cited = [c["slug"] for c in out["citations"]]
    assert "carrier-furnace" in cited
    assert "made-up-entity" not in cited  # filtered: never retrieved


def test_answer_context_is_budgeted(tmp_path, monkeypatch):
    monkeypatch.setattr(rag, "MAX_CONTEXT_CHARS", 50)
    vault = _vault(tmp_path)

    captured = {}

    class CapturingAnswerer(rag.Answerer):
        name = "cap"

        def generate(self, question, contexts):
            captured["total"] = sum(len(c["text"]) for c in contexts)
            return ""

    rag.answer(str(vault), "furnace checking", mode="fts",
               answerer=CapturingAnswerer())
    assert captured["total"] <= 50


# --- OllamaAnswerer HTTP path (stubbed) -------------------------------------

class _FakeResp:
    def __init__(self, body): self._b = body.encode("utf-8")
    def __enter__(self): return self
    def __exit__(self, *a): return False
    def read(self, amt=None): return self._b if amt is None else self._b[:amt]


def test_ollama_answerer_happy_path(monkeypatch):
    monkeypatch.setattr(rag.time, "sleep", lambda *a, **k: None)
    monkeypatch.setattr(rag.urllib.request, "urlopen",
                        lambda req, timeout=None: _FakeResp(json.dumps({"response": "It is a furnace. [[x]]"})))
    text = rag.OllamaAnswerer().generate("q", [{"slug": "x", "title": "X", "text": "t"}])
    assert "furnace" in text


def test_ollama_answerer_error_field_raises(monkeypatch):
    monkeypatch.setattr(rag.time, "sleep", lambda *a, **k: None)
    monkeypatch.setattr(rag.urllib.request, "urlopen",
                        lambda req, timeout=None: _FakeResp(json.dumps({"error": "no model"})))
    with pytest.raises(RuntimeError, match="no model"):
        rag.OllamaAnswerer().generate("q", [])

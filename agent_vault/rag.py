#!/usr/bin/env python3
"""rag.py — grounded, cited question-answering over the vault (O4.3).

Retrieval-augmented generation that stays honest to the no-hallucination ethos:
retrieve the most relevant entities (O4.1/O4.2), hand ONLY their prose+facts to
a local model as context, and require the answer to cite the entities it used.
The answer is post-checked — only citations that point at actually-retrieved
entities are reported, and `grounded` is False when the model cited nothing.

This is a READ path. It never writes vault state; the compiler remains the only
LLM *writer*. The answerer is pluggable (client/mock split like the compiler):
`OllamaAnswerer` (default) or a deterministic offline `MockAnswerer` (CI),
selected by `AGENT_VAULT_RAG=ollama|mock`.

Usage:  python -m agent_vault.rag "when does my furnace warranty expire?" [VAULT]
"""
from __future__ import annotations

import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

from agent_vault import search as _search

_CITE_RE = re.compile(r"\[\[([a-z0-9][a-z0-9._/-]*)\]\]", re.I)
MAX_CONTEXT_CHARS = int(os.environ.get("AGENT_VAULT_RAG_CONTEXT_CHARS", "6000"))


class Answerer:
    """Interface: turn a question + retrieved contexts into an answer string."""

    name = "abstract"

    def generate(self, question: str, contexts: list[dict[str, Any]]) -> str:
        raise NotImplementedError


class MockAnswerer(Answerer):
    """Deterministic, offline, extractive answerer for tests/CI.

    Does not synthesize: it quotes the top context and cites it, or declines
    when nothing was retrieved. Enough to exercise the retrieval + citation
    plumbing without a live model.
    """

    name = "mock-rag"

    def generate(self, question: str, contexts: list[dict[str, Any]]) -> str:
        if not contexts:
            return "I don't have information about that in the vault."
        top = contexts[0]
        return f"{top['text'].strip()[:240]} [[{top['slug']}]]"


class OllamaAnswerer(Answerer):
    """Local Ollama /api/generate, grounded by the retrieved contexts."""

    name = "ollama-rag"
    MAX_RESPONSE_BYTES = 8 * 1024 * 1024
    SYSTEM = (
        "You answer questions about a household knowledge vault. Use ONLY the "
        "provided context entries. If the answer is not in the context, say you "
        "don't know. Cite every entity you rely on inline as [[slug]]. Be concise "
        "and never invent facts, dates, or numbers."
    )

    def __init__(self, host: str | None = None, model: str | None = None,
                 timeout: int = 120):
        self.host = host or os.environ.get("OLLAMA_HOST", "http://localhost:11434")
        self.model = model or os.environ.get("OLLAMA_MODEL", "qwen2.5:7b-instruct")
        self.timeout = timeout
        self.name = f"ollama-rag:{self.model}"
        self.temperature = _env_float("OLLAMA_TEMPERATURE", 0.2)
        self.retries = max(0, _env_int("OLLAMA_RETRIES", 2))
        self.retry_backoff_s = _env_float("OLLAMA_RETRY_BACKOFF_S", 1.0)

    def generate(self, question: str, contexts: list[dict[str, Any]]) -> str:
        prompt = _render_prompt(question, contexts)
        url = self.host.rstrip("/") + "/api/generate"
        body = json.dumps({
            "model": self.model,
            "system": self.SYSTEM,
            "prompt": prompt,
            "stream": False,
            "options": {"temperature": self.temperature},
        }).encode("utf-8")
        req = urllib.request.Request(
            url, data=body, headers={"Content-Type": "application/json"})
        last_exc: Exception | None = None
        for attempt in range(self.retries + 1):
            try:
                with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                    raw = resp.read(self.MAX_RESPONSE_BYTES).decode("utf-8")
                payload = json.loads(raw)
                if isinstance(payload, dict) and payload.get("error"):
                    raise RuntimeError(f"Ollama error: {payload['error']}")
                return str(payload.get("response") or "").strip()
            except urllib.error.HTTPError as e:
                raise RuntimeError(f"Ollama HTTP {e.code} at {self.host}: {e.reason}") from e
            except (urllib.error.URLError, TimeoutError, ConnectionError, OSError) as e:
                last_exc = e
                if attempt < self.retries:
                    time.sleep(self.retry_backoff_s * (attempt + 1))
                    continue
            except json.JSONDecodeError as e:
                raise RuntimeError(f"Ollama returned non-JSON: {e}") from e
        raise RuntimeError(f"Ollama at {self.host} unreachable after "
                           f"{self.retries + 1} attempt(s): {last_exc}") from last_exc


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, default))
    except (TypeError, ValueError):
        return int(default)


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, default))
    except (TypeError, ValueError):
        return float(default)


def get_answerer(model_override: str | None = None) -> Answerer:
    """Return the answerer selected by AGENT_VAULT_RAG (default ollama)."""
    choice = os.environ.get("AGENT_VAULT_RAG", "ollama").lower()
    if choice == "mock":
        return MockAnswerer()
    if choice == "ollama":
        return OllamaAnswerer(model=model_override)
    raise ValueError(f"unknown AGENT_VAULT_RAG={choice!r} (expected: ollama, mock)")


def _render_prompt(question: str, contexts: list[dict[str, Any]]) -> str:
    parts = ["Context entries:"]
    for c in contexts:
        parts.append(f"[[{c['slug']}]] {c.get('title', '')} "
                     f"({c.get('type', '')}/{c.get('subtype', '')})\n{c['text']}")
    parts.append(f"\nQuestion: {question}\nAnswer (cite entities as [[slug]]):")
    return "\n\n".join(parts)


def _gather_contexts(vault: str, hits: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Load prose text for each retrieved hit, budgeted so context stays bounded."""
    contexts: list[dict[str, Any]] = []
    spent = 0
    for h in hits:
        path = Path(vault) / str(h.get("path", ""))
        text = ""
        try:
            text = _search.prose_of(path.read_text(encoding="utf-8"))
        except OSError:
            text = str(h.get("snippet", ""))
        if not text:
            text = str(h.get("snippet", "")) or str(h.get("title", ""))
        remaining = MAX_CONTEXT_CHARS - spent
        if remaining <= 0:
            break
        text = text[:remaining]
        spent += len(text)
        contexts.append({
            "slug": h.get("slug", ""), "title": h.get("title", ""),
            "type": h.get("type", ""), "subtype": h.get("subtype", ""),
            "path": h.get("path", ""), "text": text,
        })
    return contexts


def answer(vault: str, question: str, k: int = 5, mode: str = "hybrid",
           answerer: Answerer | None = None) -> dict[str, Any]:
    """Answer a question grounded in the top-k retrieved entities, with citations.

    Returns {question, answer, citations:[{slug,title,type,path}], sources:[...],
    grounded: bool, client}. `citations` are the entities the answer actually
    referenced (intersected with what was retrieved — a model can't cite outside
    the provided context); `sources` are all retrieved contexts. `grounded` is
    True iff the answer cited at least one retrieved entity.
    """
    q = (question or "").strip()
    answerer = answerer or get_answerer()
    if not q:
        return {"question": "", "answer": "", "citations": [], "sources": [],
                "grounded": False, "client": answerer.name}

    k = max(1, min(int(k), 20))
    hits = _search.search(vault, q, limit=k, mode=mode)
    contexts = _gather_contexts(vault, hits)
    by_slug = {c["slug"]: c for c in contexts}

    text = answerer.generate(q, contexts)

    cited = [s for s in dict.fromkeys(_CITE_RE.findall(text)) if s in by_slug]
    citations = [{"slug": s, "title": by_slug[s]["title"], "type": by_slug[s]["type"],
                  "path": by_slug[s]["path"]} for s in cited]
    sources = [{"slug": c["slug"], "title": c["title"], "type": c["type"],
                "path": c["path"]} for c in contexts]
    return {
        "question": q, "answer": text, "citations": citations, "sources": sources,
        "grounded": bool(citations), "client": answerer.name,
    }


def main() -> int:
    args = sys.argv[1:]
    if not args or args[0] in ("-h", "--help"):
        print(__doc__)
        return 0
    question = args[0]
    vault = args[1] if len(args) > 1 else "."
    result = answer(vault, question)
    print(result["answer"])
    if result["citations"]:
        print("\nsources: " + ", ".join(c["slug"] for c in result["citations"]))
    elif not result["grounded"]:
        print("\n(no grounded citations — answer may be unsupported)", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())

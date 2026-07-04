#!/usr/bin/env python3
"""semantic.py — vector index + semantic/hybrid retrieval (O4.2).

Builds a sidecar `_vectors.db` (SQLite) of entity embeddings and ranks a query
by cosine similarity — so an agent's natural-language question recalls the right
entities even without keyword overlap ("heating system" → the furnace page).

Deterministic and rebuildable, like the FTS sidecar; no facts of its own; git-
ignored. Embeddings come from a pluggable `embeddings.Embedder` (Ollama by
default, a deterministic MockEmbedder offline/CI). Cosine is a plain dot product
because vectors are L2-normalized on store.

Retrieval modes (see `search.search`):
  - `fts`      keyword/bm25 over prose+metadata (default; no model needed)
  - `semantic` embedding cosine (needs a built `_vectors.db`)
  - `hybrid`   reciprocal-rank fusion of fts + semantic

`build_vector_index` is NOT run by `build_index` (it may call a model/network);
build it explicitly: `python -m agent_vault.semantic build [VAULT]`.

Usage:
  python -m agent_vault.semantic build [VAULT]     # (re)build _vectors.db
  python -m agent_vault.semantic query "text" [VAULT]
"""
from __future__ import annotations

import array
import glob
import os
import sqlite3
import sys
from typing import Any

from agent_vault import search as _search
from agent_vault.embeddings import Embedder, cosine, get_embedder

try:
    import yaml
except ImportError:  # pragma: no cover
    yaml = None  # type: ignore[assignment]

DB_NAME = "_vectors.db"


def _rows(vault: str) -> list[dict[str, Any]]:
    """Parse entities into embed rows: metadata + the text to embed."""
    files = sorted(glob.glob(os.path.join(vault, "entities", "*", "*.md")))
    out: list[dict[str, Any]] = []
    for path in files:
        try:
            raw = open(path, encoding="utf-8").read()
        except OSError:
            continue
        m = _search.FM_RE.match(raw)
        if not m or yaml is None:
            continue
        try:
            fm = yaml.safe_load(m.group(1)) or {}
        except yaml.YAMLError:
            continue
        if not isinstance(fm, dict):
            continue
        prose = _search.prose_of(raw)
        title = str(fm.get("title", ""))
        # Embed title + prose: title carries the entity's identity, prose the
        # detail. Metadata-only entities still get a usable vector from title.
        text = (title + "\n" + prose).strip()
        out.append({
            "slug": str(fm.get("slug", "")),
            "title": title,
            "type": str(fm.get("type", "")),
            "subtype": str(fm.get("subtype", "")),
            "status": str(fm.get("status", "")),
            "path": os.path.relpath(path, vault).replace("\\", "/"),
            "snippet": prose[:160],
            "text": text,
        })
    return out


def build_vector_index(vault: str, embedder: Embedder | None = None) -> tuple[int, str]:
    """(Re)build `<vault>/_vectors.db`. Returns (row_count, embedder_name).

    Atomic: builds to a temp db then os.replace()s it into place so a concurrent
    query never sees a partial index.
    """
    embedder = embedder or get_embedder()
    rows = _rows(vault)
    vecs = embedder.embed([r["text"] for r in rows]) if rows else []

    tmp = os.path.join(vault, DB_NAME + ".tmp")
    if os.path.exists(tmp):
        os.remove(tmp)
    con = sqlite3.connect(tmp)
    try:
        con.execute(
            "CREATE TABLE vecs (slug TEXT, title TEXT, typ TEXT, subtype TEXT, "
            "status TEXT, path TEXT, snippet TEXT, dim INTEGER, vec BLOB)")
        con.execute("CREATE TABLE meta (k TEXT PRIMARY KEY, v TEXT)")
        con.execute("INSERT INTO meta (k, v) VALUES ('embedder', ?)", (embedder.name,))
        for r, v in zip(rows, vecs):
            blob = array.array("f", v).tobytes()
            con.execute(
                "INSERT INTO vecs (slug, title, typ, subtype, status, path, snippet, dim, vec) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (r["slug"], r["title"], r["type"], r["subtype"], r["status"],
                 r["path"], r["snippet"], len(v), blob))
        con.commit()
    finally:
        con.close()
    os.replace(tmp, os.path.join(vault, DB_NAME))
    return len(rows), embedder.name


def _load_vectors(vault: str) -> list[dict[str, Any]]:
    db = os.path.join(vault, DB_NAME)
    if not os.path.exists(db):
        return []
    con = sqlite3.connect(db)
    try:
        cur = con.execute(
            "SELECT slug, title, typ, subtype, status, path, snippet, vec FROM vecs")
        out = []
        for slug, title, typ, subtype, status, path, snippet, blob in cur.fetchall():
            a = array.array("f")
            a.frombytes(blob)
            out.append({
                "slug": slug, "title": title, "type": typ, "subtype": subtype,
                "status": status, "path": path, "snippet": snippet,
                "vec": a.tolist(),
            })
        return out
    except sqlite3.Error:
        return []
    finally:
        con.close()


def semantic_available(vault: str) -> bool:
    """True if a vector index exists to query."""
    return os.path.exists(os.path.join(vault, DB_NAME))


def semantic_search(vault: str, query: str, embedder: Embedder | None = None,
                    limit: int = 20) -> list[dict[str, Any]]:
    """Cosine-ranked semantic search over the vector index.

    Returns hits shaped like search.search(): {slug,title,type,subtype,status,
    path,score,snippet}. `score` is cosine similarity (higher is better).
    Returns [] when no vector index exists or the query is empty.
    """
    q = (query or "").strip()
    if not q:
        return []
    rows = _load_vectors(vault)
    if not rows:
        return []
    embedder = embedder or get_embedder()
    qv = embedder.embed([q])[0]
    scored = []
    for r in rows:
        s = cosine(qv, r["vec"])
        scored.append((s, r))
    scored.sort(key=lambda t: (-t[0], str(t[1]["slug"])))
    out = []
    for s, r in scored[:max(1, min(int(limit), 200))]:
        out.append({
            "slug": r["slug"], "title": r["title"], "type": r["type"],
            "subtype": r["subtype"], "status": r["status"], "path": r["path"],
            "score": round(float(s), 4), "snippet": r["snippet"],
        })
    return out


def hybrid_search(vault: str, query: str, embedder: Embedder | None = None,
                  limit: int = 20, k: int = 60) -> list[dict[str, Any]]:
    """Reciprocal-rank fusion of FTS and semantic results.

    RRF score = sum over lists of 1/(k + rank). Robust to the two rankers using
    different score scales (bm25 vs cosine). Falls back gracefully: with no
    vector index this is just the FTS ranking, and vice versa.
    """
    fts_hits = _search.search(vault, query, limit=limit * 2, mode="fts")
    sem_hits = semantic_search(vault, query, embedder=embedder, limit=limit * 2)

    fused: dict[str, dict[str, Any]] = {}
    for rank, h in enumerate(fts_hits, start=1):
        slot = fused.setdefault(h["slug"], {"hit": h, "score": 0.0})
        slot["score"] += 1.0 / (k + rank)
    for rank, h in enumerate(sem_hits, start=1):
        slot = fused.setdefault(h["slug"], {"hit": h, "score": 0.0})
        slot["score"] += 1.0 / (k + rank)

    ranked = sorted(fused.values(), key=lambda s: (-s["score"], str(s["hit"]["slug"])))
    out = []
    for slot in ranked[:max(1, min(int(limit), 200))]:
        hit = dict(slot["hit"])
        hit["score"] = round(float(slot["score"]), 6)
        out.append(hit)
    return out


def main() -> int:
    args = sys.argv[1:]
    if not args or args[0] in ("-h", "--help"):
        print(__doc__)
        return 0
    cmd = args[0]
    if cmd == "build":
        vault = args[1] if len(args) > 1 else "."
        n, name = build_vector_index(vault)
        print(f"vector index: {n} entities via {name} -> "
              f"{os.path.join(vault, DB_NAME)}")
        return 0
    if cmd == "query":
        if len(args) < 2:
            print("usage: python -m agent_vault.semantic query <text> [VAULT]",
                  file=sys.stderr)
            return 2
        query = args[1]
        vault = args[2] if len(args) > 2 else "."
        for h in semantic_search(vault, query):
            print(f"  {h['score']:.4f}  {h['slug']:30} — {h['title']}")
        return 0
    print(f"unknown command {cmd!r} (expected: build, query)", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main())

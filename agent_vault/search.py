#!/usr/bin/env python3
"""search.py — full-text, ranked search over entity prose AND metadata.

Stage 1+ retrieval. Deterministic, no LLM, no network. This is the read-side
accelerator that lets agents (and the CLI/API) search *inside* the compiled
prose bodies with relevance ranking, instead of the old substring scan that
only ever looked at slug/title/type/tags.

How it works:
  - `build_search_index(vault)` walks `entities/*/*.md`, extracts frontmatter
    metadata + the prose body (everything after the `LINKS:END` marker), and
    populates a SQLite **FTS5** sidecar at `<vault>/_index.db`. Built atomically
    (temp file + os.replace) so a concurrent reader never sees a half-built db.
  - `search(vault, query, limit)` runs a ranked FTS5 `MATCH` (bm25) and returns
    `{slug, title, type, subtype, status, path, score, snippet}` hits.

Graceful degradation: if FTS5 is not compiled into the host's SQLite, or the
`_index.db` has not been built yet, `search()` transparently falls back to a
token-overlap substring scan over `_index.json` — the same data the CLI already
had — so callers always get an answer. `fts5_available()` reports which path is
live.

The FTS db is a *derived* artifact (like `_index.json`): rebuilt from the entity
files, never authored, git-ignored. It holds no facts of its own.
"""
from __future__ import annotations

import glob
import json
import os
import re
import sqlite3
from typing import Any

try:
    import yaml
except ImportError:  # pragma: no cover - pyyaml is a hard dependency
    yaml = None  # type: ignore[assignment]

FM_RE = re.compile(r"^---\n(.*?)\n---\n(.*)$", re.S)
# The prose body begins after the ingest-owned LINKS block. Everything before
# and including LINKS:END is structural (frontmatter + generated link list) and
# must not drown out real prose in the ranking.
_LINKS_END_RE = re.compile(r"<!--\s*LINKS:END\s*-->", re.I)
# Query tokens are reduced to alphanumerics so the FTS5 MATCH expression we build
# is always syntactically valid (no operators, quotes, or parens leak in).
_TOKEN_RE = re.compile(r"[a-z0-9]+")

DB_NAME = "_index.db"
INDEX_NAME = "_index.json"

# Column layout of the FTS5 table. UNINDEXED columns are stored for retrieval
# but not tokenized. `body` is the last column; its 0-based index is used by
# snippet() below.
_COLUMNS = (
    "slug UNINDEXED", "path UNINDEXED", "status UNINDEXED",
    "title", "aliases", "tags", "typ", "subtype", "body",
)
_BODY_COL = 8


_FTS5_CACHE: bool | None = None


def fts5_available() -> bool:
    """Return True if this SQLite build has the FTS5 extension. Cached."""
    global _FTS5_CACHE
    if _FTS5_CACHE is None:
        try:
            con = sqlite3.connect(":memory:")
            try:
                con.execute("CREATE VIRTUAL TABLE _probe USING fts5(x)")
                _FTS5_CACHE = True
            finally:
                con.close()
        except sqlite3.Error:
            _FTS5_CACHE = False
    return _FTS5_CACHE


def prose_of(raw: str) -> str:
    """Extract the prose body from a raw entity file.

    Prose is everything after the `LINKS:END` marker. If there is no LINKS
    block (older/hand-authored files), fall back to everything after the
    frontmatter fence so search still sees the text.
    """
    m = _LINKS_END_RE.search(raw)
    if m:
        return raw[m.end():].strip()
    fm = FM_RE.match(raw)
    if fm:
        return fm.group(2).strip()
    return raw.strip()


def _row_for(path: str, vault: str) -> dict[str, Any] | None:
    """Parse one entity file into an FTS row dict, or None if unreadable."""
    try:
        raw = open(path, encoding="utf-8").read()
    except OSError:
        return None
    m = FM_RE.match(raw)
    if not m or yaml is None:
        return None
    try:
        fm = yaml.safe_load(m.group(1)) or {}
    except yaml.YAMLError:
        return None
    if not isinstance(fm, dict):
        return None
    tags = fm.get("tags") or []
    aliases = fm.get("aliases") or []
    return {
        "slug": str(fm.get("slug", "")),
        "path": os.path.relpath(path, vault).replace("\\", "/"),
        "status": str(fm.get("status", "")),
        "title": str(fm.get("title", "")),
        "aliases": " ".join(str(a) for a in aliases) if isinstance(aliases, list) else str(aliases),
        "tags": " ".join(str(t) for t in tags) if isinstance(tags, list) else str(tags),
        "typ": str(fm.get("type", "")),
        "subtype": str(fm.get("subtype", "")),
        "body": prose_of(raw),
    }


def build_search_index(vault: str) -> int:
    """(Re)build the FTS5 sidecar at `<vault>/_index.db`. Returns row count.

    Atomic: writes a fresh db to a temp path then os.replace()s it over the
    live file, so a concurrent `search()` never observes a partial index.
    No-op (returns 0) when FTS5 is unavailable — callers just fall back.
    """
    if not fts5_available():
        return 0
    files = sorted(glob.glob(os.path.join(vault, "entities", "*", "*.md")))
    rows = [r for r in (_row_for(f, vault) for f in files) if r]

    tmp = os.path.join(vault, DB_NAME + ".tmp")
    if os.path.exists(tmp):
        os.remove(tmp)
    con = sqlite3.connect(tmp)
    try:
        con.execute(f"CREATE VIRTUAL TABLE entities_fts USING fts5({', '.join(_COLUMNS)}, "
                    f"tokenize = 'unicode61 remove_diacritics 2')")
        con.executemany(
            "INSERT INTO entities_fts "
            "(slug, path, status, title, aliases, tags, typ, subtype, body) "
            "VALUES (:slug, :path, :status, :title, :aliases, :tags, :typ, :subtype, :body)",
            rows,
        )
        con.commit()
    finally:
        con.close()
    os.replace(tmp, os.path.join(vault, DB_NAME))
    return len(rows)


def _match_expr(query: str) -> str:
    """Turn free text into a safe FTS5 MATCH expression.

    Each token is reduced to alphanumerics and prefix-matched; tokens are OR-ed
    so partial-overlap queries still recall (bm25 then ranks documents matching
    more/rarer terms higher). Returns "" when the query has no usable token.
    The result is passed as a *bound parameter*, never string-interpolated into
    SQL, so it cannot inject.
    """
    tokens = _TOKEN_RE.findall(query.lower())
    return " OR ".join(f"{t}*" for t in tokens)


def _fts_search(vault: str, query: str, limit: int) -> list[dict[str, Any]]:
    expr = _match_expr(query)
    if not expr:
        return []
    db = os.path.join(vault, DB_NAME)
    if not os.path.exists(db):
        return []
    con = sqlite3.connect(db)
    try:
        cur = con.execute(
            "SELECT slug, title, typ, subtype, status, path, "
            "       bm25(entities_fts) AS score, "
            f"      snippet(entities_fts, {_BODY_COL}, '', '', '…', 12) AS snip "
            "FROM entities_fts WHERE entities_fts MATCH ? "
            "ORDER BY score LIMIT ?",
            (expr, limit),
        )
        out = []
        for slug, title, typ, subtype, status, path, score, snip in cur.fetchall():
            out.append({
                "slug": slug, "title": title, "type": typ, "subtype": subtype,
                "status": status, "path": path,
                "score": round(float(score), 4), "snippet": snip,
            })
        return out
    except sqlite3.Error:
        return []
    finally:
        con.close()


def _fallback_search(vault: str, query: str, limit: int) -> list[dict[str, Any]]:
    """Token-overlap substring scan over _index.json (metadata only).

    Used when FTS5 or the sidecar db is unavailable. Ranks by how many query
    tokens are present, so it is at least ordered — but it cannot see prose.
    """
    p = os.path.join(vault, INDEX_NAME)
    if not os.path.exists(p):
        return []
    try:
        entities = json.load(open(p, encoding="utf-8")).get("entities", [])
    except (OSError, json.JSONDecodeError):
        return []
    tokens = _TOKEN_RE.findall(query.lower())
    if not tokens:
        return []
    scored = []
    for e in entities:
        hay = " ".join([
            str(e.get("slug", "")), str(e.get("title", "")), str(e.get("type", "")),
            str(e.get("subtype", "")), " ".join(str(t) for t in e.get("tags", [])),
            " ".join(str(m) for m in e.get("_match", [])),
        ]).lower()
        hits = sum(1 for t in tokens if t in hay)
        if hits:
            scored.append((hits, e))
    scored.sort(key=lambda r: (-r[0], str(r[1].get("slug", ""))))
    out = []
    for hits, e in scored[:limit]:
        out.append({
            "slug": e.get("slug", ""), "title": e.get("title", ""),
            "type": e.get("type", ""), "subtype": e.get("subtype", ""),
            "status": e.get("status", ""), "path": e.get("path", ""),
            "score": float(hits), "snippet": "",
        })
    return out


def search(vault: str, query: str, limit: int = 20,
           mode: str = "fts") -> list[dict[str, Any]]:
    """Ranked search over prose + metadata.

    mode:
      - "fts" (default) — FTS5 bm25 over prose+metadata (metadata fallback if
        the sidecar is unavailable). No model needed.
      - "semantic" — embedding cosine over `_vectors.db` (O4.2). Requires a
        built vector index; returns [] if absent.
      - "hybrid" — reciprocal-rank fusion of fts + semantic.

    Always returns a list of hit dicts:
    {slug, title, type, subtype, status, path, score, snippet}.
    """
    limit = max(1, min(int(limit), 200))
    if mode in ("semantic", "hybrid"):
        # Imported lazily so the common FTS path never pays for the embedding
        # stack (and so environments without an embedder still import search).
        from agent_vault import semantic
        if mode == "semantic":
            return semantic.semantic_search(vault, query, limit=limit)
        return semantic.hybrid_search(vault, query, limit=limit)

    hits = _fts_search(vault, query, limit)
    if hits:
        return hits
    # Empty could mean "no FTS db / no FTS5" OR "genuinely no prose matches".
    # Fall back only when the sidecar isn't usable, so a real zero-result FTS
    # query doesn't get masked by weaker metadata matches.
    if not fts5_available() or not os.path.exists(os.path.join(vault, DB_NAME)):
        return _fallback_search(vault, query, limit)
    return hits

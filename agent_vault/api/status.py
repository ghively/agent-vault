"""Dashboard endpoints for Agent Vault API: GET /api/status and GET /api/ask.

Pure in-process reads over _index.json (same data the CLI `synapse` commands
filter — the date math is imported from agent_vault.synapse rather than
re-implemented) plus the last cadence run from discovery/_runs.jsonl.
Shapes match the web UI contract in web/src/api/types.ts (StatusResponse,
AskResponse).
"""

from __future__ import annotations

import glob
import json
import os
from collections.abc import Callable
from pathlib import Path
from typing import Any, cast

from fastapi import APIRouter, Depends, Query, Request

from agent_vault.api.config import Settings
from agent_vault.api.history import read_jsonl
from agent_vault.api.reads import load_index
from agent_vault import synapse as _synapse

# agent_vault.synapse is legacy-untyped (mypy override) — cast its two pure
# helpers to typed callables so this strict module can use them.
days_until = cast(Callable[[Any], "int | None"], _synapse.days_until)
norm = cast(Callable[[Any], str], _synapse.norm)

router = APIRouter()

DUE_WINDOW_DAYS = 30  # matches `synapse due` default
EXPIRING_WINDOW_DAYS = 90  # matches `synapse expiring` default


async def get_settings(request: Request) -> Settings:
    """Get settings, with resolved vault_path from request state (MTAV)."""
    s = request.app.state.settings  # type: ignore
    vault_path = getattr(request.state, "vault_path", "") or ""
    if vault_path and vault_path != s.vault_path:
        from dataclasses import replace
        return replace(s, vault_path=vault_path)
    return s


def _due_items(ents: list[dict[str, Any]], days: int = DUE_WINDOW_DAYS) -> list[dict[str, Any]]:
    """Entities with a `due` date within N days (same filter as `synapse due`)."""
    rows = []
    for e in ents:
        d = (e.get("dates") or {}).get("due")
        if not d:
            continue
        n = days_until(d)
        if n is not None and n <= days:
            rows.append(
                {"slug": e.get("slug"), "title": e.get("title", ""), "date": str(d), "days": n}
            )
    rows.sort(key=lambda r: r["days"])
    return rows


def _expiring_items(
    ents: list[dict[str, Any]], days: int = EXPIRING_WINDOW_DAYS
) -> list[dict[str, Any]]:
    """Entities with an `expires`/`renews` date within N days (as `synapse expiring`)."""
    rows = []
    for e in ents:
        for fld in ("expires", "renews"):
            d = (e.get("dates") or {}).get(fld)
            if not d:
                continue
            n = days_until(d)
            if n is not None and n <= days:
                rows.append(
                    {
                        "slug": e.get("slug"),
                        "title": e.get("title", ""),
                        "date": str(d),
                        "days": n,
                        "field": fld,
                    }
                )
    rows.sort(key=lambda r: r["days"])
    return rows


def _find_items(ents: list[dict[str, Any]], q: str) -> list[dict[str, Any]]:
    """Substring search across slug/title/type/subtype/tags/_match — the same
    haystack `synapse find` builds."""
    nq = norm(q)
    if not nq:
        return []
    hits = []
    for e in ents:
        hay = " ".join(
            [
                e.get("slug", ""),
                e.get("title", ""),
                e.get("type", ""),
                e.get("subtype", ""),
                " ".join(e.get("tags", [])),
                " ".join(e.get("_match", [])),
            ]
        ).lower()
        if nq in hay:
            hits.append(
                {
                    "slug": e.get("slug"),
                    "title": e.get("title", ""),
                    "type": e.get("type", ""),
                    "subtype": e.get("subtype", ""),
                    "tags": e.get("tags", []),
                }
            )
    return hits


def _index_status(vault: Path) -> dict[str, Any]:
    """Report index freshness so readers can detect a stale `_index.json`.

    `stale` is True when any entity file has been modified more recently than
    the index was built — i.e. a write happened without a reindex. In normal
    operation every mutating pass reindexes, so this stays False; it surfaces
    the multi-writer edge where a direct file edit bypassed the pipeline.
    """
    idx = vault / "_index.json"
    if not idx.exists():
        return {"generated": None, "count": 0, "stale": True}
    try:
        data = json.loads(idx.read_text(encoding="utf-8"))
        generated = data.get("generated")
        count = int(data.get("count", 0))
    except (OSError, json.JSONDecodeError, ValueError, TypeError):
        return {"generated": None, "count": 0, "stale": True}
    idx_mtime = idx.stat().st_mtime
    stale = False
    for f in glob.glob(os.path.join(str(vault), "entities", "*", "*.md")):
        try:
            if os.path.getmtime(f) > idx_mtime + 1:  # 1s slack for write ordering
                stale = True
                break
        except OSError:
            continue
    return {"generated": generated, "count": count, "stale": stale}


def _last_run(vault: Path) -> dict[str, Any] | None:
    """Last line of discovery/_runs.jsonl (shape from cadences/run_cadence.py)."""
    runs = read_jsonl(vault / "discovery" / "_runs.jsonl")
    if not runs:
        return None
    rec = runs[-1]
    return {
        "cadence": rec.get("cadence", ""),
        "ts": rec.get("ts", ""),
        "rc": rec.get("rc"),
        "duration_s": rec.get("duration_s"),
        "detail": rec.get("detail", ""),
    }


@router.get("/status")
async def get_status(s: Settings = Depends(get_settings)) -> dict[str, Any]:
    """Vault dashboard snapshot: counts, due/expiring items, type breakdown,
    and the last cadence run."""
    vault = Path(s.vault_path)
    ents = load_index(vault)

    breakdown: dict[str, int] = {}
    compiled = needs_review = 0
    for e in ents:
        t = e.get("type") or "unknown"
        breakdown[t] = breakdown.get(t, 0) + 1
        status = e.get("status")
        if status == "compiled":
            compiled += 1
        elif status == "needs-review":
            needs_review += 1

    return {
        "counts": {"total": len(ents), "compiled": compiled, "needs_review": needs_review},
        "due": _due_items(ents),
        "expiring": _expiring_items(ents),
        "breakdown": breakdown,
        "last_run": _last_run(vault),
        "index": _index_status(vault),
    }


@router.get("/ask")
async def ask(
    q: str = Query(..., description="Natural-language-ish query"),
    s: Settings = Depends(get_settings),
) -> dict[str, Any]:
    """Deterministic query routing (no LLM): 'due' → due items, 'expir'/'renew'
    → expiring items, anything else → index substring search."""
    vault = Path(s.vault_path)
    ents = load_index(vault)
    ql = q.lower()

    if "due" in ql:
        return {"intent": "due", "items": _due_items(ents)}
    if "expir" in ql or "renew" in ql:
        return {"intent": "expiring", "items": _expiring_items(ents)}
    return {"intent": "find", "items": _find_items(ents, q)}


@router.get("/answer")
async def answer(
    q: str = Query(..., description="Natural-language question"),
    k: int = Query(5, ge=1, le=20),
    mode: str = Query("hybrid", pattern="^(fts|semantic|hybrid)$"),
    s: Settings = Depends(get_settings),
) -> dict[str, Any]:
    """Grounded, cited RAG answer over the vault (O4.3).

    Retrieves the top-`k` entities (via `mode`), hands only their prose to a
    local model, and requires citations — reported citations are intersected
    with what was retrieved, so the answer can't cite outside its context.
    `grounded` is False when the answer cited nothing. Distinct from GET
    /api/ask (deterministic routing, no LLM). Returns {question, answer,
    citations, sources, grounded, client}.
    """
    from agent_vault import rag

    return rag.answer(str(Path(s.vault_path)), q, k=k, mode=mode)

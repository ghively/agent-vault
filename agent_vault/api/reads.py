"""Read endpoints for Agent Vault API.

All endpoints are in-process, lock-free reads from the vault data.
Mirrors SynapseNAS server/vault.py shapes for Phase 3/4 compatibility.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import yaml
from fastapi import APIRouter, Depends, HTTPException, Query, Request

from agent_vault.api.config import Settings

router = APIRouter()

# Reuse patterns from SynapseNAS vault.py
_FM_RE = re.compile(r"^---\n(.*?)\n---\n(.*)$", re.S)
_LINKS_RE = re.compile(r"<!-- LINKS:BEGIN -->(.*?)<!-- LINKS:END -->", re.S)
_WIKILINK_RE = re.compile(r"\[\[([^\]]+)\]\]")
_CRED_RE = re.compile(r"credential_ref:\s*(\S+)")


async def get_settings(request: Request) -> Settings:
    """Get settings from app state (dependency injection)."""
    return request.app.state.settings  # type: ignore


def load_index(vault: Path) -> list[dict[str, Any]]:
    """Load _index.json from vault."""
    p = vault / "_index.json"
    if not p.exists():
        return []
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return []
    ents = data.get("entities", []) if isinstance(data, dict) else []
    return ents if isinstance(ents, list) else []


def parse_entity_file(path: Path) -> dict[str, Any]:
    """Parse entity .md file into frontmatter, links_block, and prose.

    Returns {} if the file vanished (a concurrent mutating job can delete/rename
    it between the index load and this read) or is malformed — the caller then
    404s cleanly instead of surfacing an unhandled 500 (B9).
    """
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError:
        return {}
    m = _FM_RE.match(raw)
    if not m:
        return {}
    fm = yaml.safe_load(m.group(1)) or {}
    body = m.group(2)
    lm = _LINKS_RE.search(body)
    links_block = lm.group(1).strip() if lm else ""
    prose = _LINKS_RE.sub("", body).strip()
    return {"frontmatter": fm, "links_block": links_block, "prose": prose}


def read_credential_ref(path: Path) -> str | None:
    """Extract credential_ref from entity file."""
    m = _CRED_RE.search(path.read_text(encoding="utf-8"))
    return m.group(1) if m else None


FACT_FIELDS = [
    "location",
    "acquired",
    "expires",
    "renews",
    "serviced",
    "due",
    "model",
    "serial",
    "vin",
    "last4",
    "value",
    "value_as_of",
]


def build_facts(fm: dict[str, Any]) -> list[dict[str, str]]:
    """Build facts list from frontmatter (matching SynapseNAS shape)."""
    out = []
    for k in FACT_FIELDS:
        v = fm.get(k)
        if v not in (None, "", []):
            out.append({"k": k, "v": str(v)})
    return out


def parse_links(links_block: str) -> list[dict[str, str]]:
    """Parse wikilinks from links block."""
    rows: list[dict[str, str]] = []
    for line in links_block.splitlines():
        line = line.strip()
        if not line.startswith("-"):
            continue
        label, _, rest = line[1:].partition(":")
        for ref in _WIKILINK_RE.findall(rest):
            rows.append({"label": label.strip(), "ref": ref.strip()})
    return rows


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    """Read JSONL file into list of dicts."""
    if not path.exists():
        return []
    out = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return out


@router.get("/entities")
async def list_entities(
    q: str = "",
    type_: str = Query("", alias="type"),
    s: Settings = Depends(get_settings),
) -> dict[str, Any]:
    """List entity summaries with optional search and type filter.

    Args:
        q: Search query (filters slug, type/subtype, status, tags)
        type_: Filter by entity type
        s: Settings (dependency injected)

    Returns:
        Dict with 'rows' (matching entities) and 'total' (all entities count)
    """
    vault = Path(s.vault_path)
    rows = []
    ql = q.strip().lower()
    index = load_index(vault)  # bind once; reused for the total below (B8)
    for e in index:
        ts = f"{e.get('type', '')}/{e.get('subtype', '')}"
        if type_ and e.get("type") != type_:
            continue
        hay = (
            e.get("slug", "")
            + " "
            + ts
            + " "
            + e.get("status", "")
            + " "
            + " ".join(e.get("tags", []))
        ).lower()
        if ql and ql not in hay:
            continue
        rows.append(
            {
                "slug": e.get("slug"),
                "ts": ts,
                "type": e.get("type"),
                "subtype": e.get("subtype"),
                "status": e.get("status"),
                "confidence": e.get("confidence"),
                "tags": e.get("tags", []),
            }
        )
    return {"rows": rows, "total": len(index)}


@router.get("/search")
async def search_entities(
    q: str = Query("", description="free-text query over prose + metadata"),
    limit: int = Query(20, ge=1, le=200),
    mode: str = Query("fts", pattern="^(fts|semantic|hybrid)$"),
    s: Settings = Depends(get_settings),
) -> dict[str, Any]:
    """Ranked retrieval over entity prose AND metadata.

    `mode`:
      - `fts` (default) — prose-aware bm25 via the FTS5 sidecar (`_index.db`),
        with a metadata token-overlap fallback. No model needed.
      - `semantic` — embedding cosine over the vector index (`_vectors.db`,
        built via `python -m agent_vault.semantic build`). Empty if not built.
      - `hybrid` — reciprocal-rank fusion of fts + semantic.

    Unlike GET /api/entities (a metadata filter), this is the agent-facing
    *retrieval* surface. Returns {query, mode, hits:[{slug,title,type,subtype,
    status,path,score,snippet}], fts, semantic} where the last two report which
    ranked paths are live.
    """
    from agent_vault import search as search_mod
    from agent_vault import semantic as semantic_mod

    vault = Path(s.vault_path)
    query = q.strip()
    caps = {"fts": search_mod.fts5_available(),
            "semantic": semantic_mod.semantic_available(str(vault))}
    if not query:
        return {"query": "", "mode": mode, "hits": [], **caps}
    hits = search_mod.search(str(vault), query, limit, mode=mode)
    return {"query": query, "mode": mode, "hits": hits, **caps}


def build_entity_detail(vault: Path, slug: str, path: Path) -> dict[str, Any]:
    """Build the full entity-detail dict served by GET /api/entities/{slug}.

    Shared with the edit endpoints (edit.py) so a PATCH response is
    byte-compatible with a subsequent GET.
    """
    by_slug = {e["slug"]: e for e in load_index(vault) if e.get("slug")}
    parsed = parse_entity_file(path)
    if not parsed:
        # File vanished (concurrent mutation) or is unparseable — 404, not 500.
        raise HTTPException(status_code=404, detail="entity file not readable")
    fm = parsed.get("frontmatter", {})

    # Resolve links
    links = []
    for row in parse_links(parsed.get("links_block", "")):
        target = row["ref"].split("/")[-1]
        tgt = by_slug.get(target)
        links.append(
            {
                "label": row["label"],
                "ref": row["ref"],
                # Index rows omit `title` when the entity's frontmatter has none
                # (build_index only copies non-empty passthrough fields), so use
                # .get() with the slug-derived fallback instead of tgt["title"],
                # which would KeyError -> 500 on a titleless linked entity.
                "title": (tgt.get("title") if tgt else None)
                or target.replace("-", " ").title(),
                "exists": tgt is not None,
            }
        )

    return {
        "slug": slug,
        "title": fm.get("title", slug),
        "type": fm.get("type"),
        "subtype": fm.get("subtype"),
        "status": fm.get("status"),
        "confidence": fm.get("confidence"),
        "hash": fm.get("sources_hash"),
        "prose": parsed.get("prose", ""),
        "sources": fm.get("sources", []),
        "facts": build_facts(fm),
        "links": links,
        # Editable fields surfaced verbatim so the edit UI prefills from the
        # authoritative source rather than the entities-list row.
        "tags": [str(t) for t in fm.get("tags") or [] if t is not None],
        "notes": "" if fm.get("notes") is None else str(fm.get("notes")),
    }


@router.get("/entities/{slug}")
def get_entity(
    slug: str,
    s: Settings = Depends(get_settings),
) -> dict[str, Any]:
    """Get full entity record with resolved markdown body.

    Args:
        slug: Entity slug
        s: Settings (dependency injected)

    Returns:
        Full entity dict with prose, sources, facts, links

    Raises:
        HTTPException: 404 if entity not found
    """
    vault = Path(s.vault_path)
    by_slug = {e["slug"]: e for e in load_index(vault) if e.get("slug")}
    rec = by_slug.get(slug)
    if not rec or not rec.get("path"):
        raise HTTPException(status_code=404, detail="entity not found")
    return build_entity_detail(vault, slug, vault / rec["path"])


@router.get("/review/proposals")
def list_proposals(
    s: Settings = Depends(get_settings),
) -> dict[str, Any]:
    """List pending promote/compile proposals.

    Args:
        s: Settings (dependency injected)

    Returns:
        Dict with 'items' list of pending proposals
    """
    import hashlib

    vault = Path(s.vault_path)
    log = read_jsonl(vault / "discovery" / "promoted.jsonl")
    latest: dict[tuple[Any, ...], dict[str, Any]] = {}

    for rec in log:
        ident = tuple(rec.get("identity") or ())
        if ident:
            latest[ident] = rec

    out = []
    for ident, rec in latest.items():
        if rec.get("action") == "queue_for_review":
            # Short ID matching SynapseNAS pattern
            short_id = hashlib.sha256(json.dumps(list(ident)).encode()).hexdigest()[:8]
            out.append(
                {
                    "id": short_id,
                    "kind": rec.get("kind"),
                    "concept": "/".join(str(x) for x in ident[1:]) or str(ident),
                    "sightings": rec.get("sightings", 0),
                    "reason": rec.get("reason", ""),
                }
            )
    out.sort(key=lambda r: r["concept"])
    return {"items": out}


@router.get("/review/entities")
def list_review_entities(
    s: Settings = Depends(get_settings),
) -> dict[str, Any]:
    """List pending entity-review queue.

    Args:
        s: Settings (dependency injected)

    Returns:
        Dict with 'items' list of entities needing review
    """
    vault = Path(s.vault_path)

    # Load gated types from schema
    gated = set()
    schema_path = vault / "registry" / "schema.yaml"
    if schema_path.exists():
        try:
            schema = yaml.safe_load(schema_path.read_text(encoding="utf-8")) or {}
            for t, spec in (schema.get("types") or {}).items():
                if isinstance(spec, dict) and spec.get("human_gated"):
                    gated.add(t)
        except (OSError, yaml.YAMLError):
            pass

    out = []
    entities_dir = vault / "entities"
    if entities_dir.exists():
        for path in sorted(entities_dir.glob("*/*.md")):
            parsed = parse_entity_file(path)
            fm = parsed.get("frontmatter", {})
            if fm.get("status") != "needs-review":
                continue
            t = path.parent.name
            out.append(
                {
                    "ref": f"{t}/{path.stem}",
                    "title": fm.get("title", ""),
                    "confidence": fm.get("confidence"),
                    "gated": t in gated,
                    "inferred": bool(fm.get("inferred")),
                }
            )
    return {"items": out}


@router.get("/creds")
def list_creds(
    s: Settings = Depends(get_settings),
) -> dict[str, Any]:
    """List credential references per entity (NOT plaintext secrets).

    Args:
        s: Settings (dependency injected)

    Returns:
        Dict with 'items' list of credential refs (type/slug → ref + backend)
    """
    vault = Path(s.vault_path)
    out = []
    for e in load_index(vault):
        if not e.get("has_credential"):
            continue
        ref = read_credential_ref(vault / e["path"]) or ""
        backend = ref.split("://", 1)[0] if "://" in ref else ""
        out.append(
            {"slug": e.get("slug"), "title": e.get("title"), "ref": ref, "backend": backend}
        )
    return {"items": out}

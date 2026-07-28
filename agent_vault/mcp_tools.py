#!/usr/bin/env python3
"""mcp_tools.py — the vault operations exposed to agents over MCP.

These are plain, JSON-returning functions (no MCP transport, no network) so they
are independently testable and reusable. `mcp_server.py` wraps them as MCP tools.

Agent Vault is a shared knowledgebase multiple agents read from and write to.
Every function here upholds the core invariants:
  - Reads (`search`, `get`, `list`, `status`) are lock-free and never mutate.
  - The only write (`submit_source`) appends a NEW file to `raw/` (append-only)
    and refuses to overwrite; deterministic `ingest` classifies it later. It
    never touches the registry/vocabulary or an entity's facts.
  - `resolve_credential` returns a secret only when explicitly enabled, and the
    secret is never logged.
Every write is stamped with the calling agent's `actor` id for attribution.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from agent_vault import search as _search
from agent_vault.api import reads as _reads

_SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,127}$")
# raw/ filenames: no path separators, no leading dot/dash, bounded length.
_FILENAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,159}$")
# raw/ subdirectory: a single safe path segment.
_SUBDIR_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")

# Opt-in gate for the one tool that returns plaintext secrets. Off by default so
# a stray agent can't exfiltrate credentials just by connecting.
_RESOLVE_ENV = "AGENT_VAULT_MCP_ALLOW_RESOLVE"


def _err(msg: str) -> dict[str, Any]:
    return {"error": msg}


def search(vault: str, query: str, limit: int = 20, mode: str = "fts") -> dict[str, Any]:
    """Ranked retrieval over entity prose AND metadata.

    `mode`: "fts" (default, keyword/bm25, no model), "semantic" (embedding
    cosine over the vector index), or "hybrid" (rank-fusion of both). Returns
    {query, mode, hits:[{slug,title,type,subtype,status,path,score,snippet}],
    fts, semantic} where the last two report which ranked paths are available.
    This is the primary way an agent recalls knowledge from the wiki.
    """
    from agent_vault import semantic as _semantic
    if mode not in ("fts", "semantic", "hybrid"):
        return _err("mode must be one of: fts, semantic, hybrid")
    q = (query or "").strip()
    caps = {"fts": _search.fts5_available(), "semantic": _semantic.semantic_available(vault)}
    if not q:
        return {"query": "", "mode": mode, "hits": [], **caps}
    hits = _search.search(vault, q, limit, mode=mode)
    return {"query": q, "mode": mode, "hits": hits, **caps}


def ask(vault: str, question: str, k: int = 5, mode: str = "hybrid") -> dict[str, Any]:
    """Grounded, cited answer to a natural-language question over the vault.

    Retrieves the top-`k` entities (mode: fts/semantic/hybrid), grounds a local
    model on their prose, and returns {question, answer, citations, sources,
    grounded, client}. `citations` are the entities the answer actually
    referenced; `grounded` is False if it cited nothing. Answers never invent
    facts — they're constrained to the retrieved context.
    """
    from agent_vault import rag
    if mode not in ("fts", "semantic", "hybrid"):
        return _err("mode must be one of: fts, semantic, hybrid")
    q = (question or "").strip()
    if not q:
        return _err("question is required")
    return rag.answer(vault, q, k=k, mode=mode)


def get_entity(vault: str, slug: str) -> dict[str, Any]:
    """Full record for one entity: facts, prose, sources, and resolved links.

    Returns the entity detail dict, or {"error": ...} if the slug is invalid or
    unknown. Does not include secrets — a `credential_ref` is reported as a
    reference only (use resolve_credential to fetch the secret).
    """
    if not _SLUG_RE.match(slug or ""):
        return _err("invalid slug format")
    vpath = Path(vault)
    entity = next((e for e in _reads.load_index(vpath) if e.get("slug") == slug), None)
    if not entity or not entity.get("path"):
        return _err("entity not found")
    epath = vpath / entity["path"]
    if not epath.exists():
        return _err("entity file not found")
    try:
        detail = _reads.build_entity_detail(vpath, slug, epath)
    except OSError as e:
        return _err(f"could not read entity: {e}")
    # Surface extra frontmatter fields (make, model, serial, etc.) that
    # build_entity_detail doesn't lift into the canonical response shape.
    # MCP consumers need these to answer real questions ("what's the serial?").
    try:
        raw = epath.read_text(encoding="utf-8")
        import re as _re
        import yaml as _yaml
        m = _re.match(r"^---\n(.*?)\n---\n", raw, _re.S)
        if m:
            extra_fm = _yaml.safe_load(m.group(1)) or {}
            known = set(detail.keys()) | {"facts", "links", "notes", "hash"}
            for k, v in extra_fm.items():
                if k not in known and k not in ("related",):
                    detail[k] = v
    except Exception:  # noqa: BLE001
        pass
    return detail


def list_entities(vault: str, type_: str = "", limit: int = 100) -> dict[str, Any]:
    """List entity summaries, optionally filtered by type. Returns {rows,total}."""
    limit = max(1, min(int(limit), 1000))
    vpath = Path(vault)
    index = _reads.load_index(vpath)
    rows = []
    for e in index:
        if type_ and e.get("type") != type_:
            continue
        rows.append({
            "slug": e.get("slug"),
            "type": e.get("type"),
            "subtype": e.get("subtype"),
            "status": e.get("status"),
            "title": e.get("title", ""),
            "tags": e.get("tags", []),
        })
        if len(rows) >= limit:
            break
    return {"rows": rows, "total": len(index)}


def status(vault: str) -> dict[str, Any]:
    """Vault snapshot: entity counts by status/type + the last pipeline run."""
    vpath = Path(vault)
    index = _reads.load_index(vpath)
    breakdown: dict[str, int] = {}
    compiled = needs_review = 0
    for e in index:
        t = str(e.get("type") or "unknown")
        breakdown[t] = breakdown.get(t, 0) + 1
        st = e.get("status")
        if st == "compiled":
            compiled += 1
        elif st == "needs-review":
            needs_review += 1
    runs_path = vpath / "discovery" / "_runs.jsonl"
    last_run = None
    if runs_path.exists():
        recs = [
            json.loads(x) for x in runs_path.read_text(encoding="utf-8").splitlines()
            if x.strip()
        ] if runs_path.exists() else []
        if recs:
            last_run = recs[-1]
    return {
        "counts": {"total": len(index), "compiled": compiled, "needs_review": needs_review},
        "breakdown": breakdown,
        "last_run": last_run,
    }


def submit_source(vault: str, filename: str, content: str,
                  subdir: str = "documents", actor: str = "") -> dict[str, Any]:
    """Contribute a NEW source document to the vault (the agent write path).

    Writes `content` to `raw/<subdir>/<filename>` so the next `ingest` pass
    classifies it deterministically into the wiki. Honors the append-only
    invariant: it creates a new file only and REFUSES to overwrite an existing
    one. Records an attribution line (actor, path, sha256, bytes) to
    `discovery/_submissions.jsonl`.

    Returns {ok, path, sha256, bytes} or {error}.
    """
    if not _FILENAME_RE.match(filename or ""):
        return _err("invalid filename (no path separators; must match "
                    "[A-Za-z0-9][A-Za-z0-9._-]{0,159})")
    if not _SUBDIR_RE.match(subdir or ""):
        return _err("invalid subdir (single safe path segment)")
    if content is None:
        return _err("content is required")

    vpath = Path(vault)
    raw_dir = vpath / "raw" / subdir
    dest = raw_dir / filename
    # Defense in depth: the resolved path must stay inside raw/ even if the
    # regexes were ever loosened.
    raw_root = (vpath / "raw").resolve()
    if raw_root not in dest.resolve().parents and dest.resolve() != raw_root:
        return _err("path escapes raw/")
    if dest.exists():
        return _err(f"raw/{subdir}/{filename} already exists (raw/ is append-only)")

    data = content.encode("utf-8")
    sha = hashlib.sha256(data).hexdigest()
    try:
        raw_dir.mkdir(parents=True, exist_ok=True)
        tmp = dest.with_suffix(dest.suffix + ".tmp")
        with open(tmp, "wb") as fh:
            fh.write(data)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, dest)
    except OSError as e:
        return _err(f"could not write source: {e}")

    rel = f"raw/{subdir}/{filename}"
    _append_submission(vpath, actor, rel, sha, len(data))
    return {"ok": True, "path": rel, "sha256": sha, "bytes": len(data)}



def create(vault: str, slug: str, type_: str, subtype: str,
           data: dict[str, Any] | None = None, prose: str = "") -> dict[str, Any]:
    """Create a new entity from structured data (frontmatter only, no LLM).

    The deterministic write path for structured knowledge — accounts, assets,
    credentials, anything where the facts are already known and don't need the
    source-document → ingest → compile pipeline. Validates type/subtype against
    registry/schema.yaml, builds a well-formed entity file with the canonical
    3-region format (frontmatter / LINKS block / prose), and rebuilds _index.json
    so the new entity is immediately findable.

    `data` carries optional frontmatter fields (title, tags, location, serial,
    model, last4, credential_ref, related, etc.). Anything not provided is
    defaulted: title from slug, status='stub', confidence=1.0, today's date.
    `prose` is appended verbatim below the LINKS block if given.

    Returns {ok, slug, path} or {error}.
    """
    if not _SLUG_RE.match(slug or ""):
        return _err("invalid slug format")
    vpath = Path(vault)

    # --- validate type/subtype against registry/schema.yaml ---
    schema_path = vpath / "registry" / "schema.yaml"
    if not schema_path.exists():
        return _err("registry/schema.yaml not found — not a valid vault")
    try:
        import yaml
        schema = yaml.safe_load(schema_path.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError as e:
        return _err(f"schema.yaml is not valid YAML: {e}")
    types = schema.get("types", {})
    if type_ not in types:
        return _err(f"unknown type '{type_}' (valid: {sorted(types)})")
    type_spec = types[type_]
    if not isinstance(type_spec, dict):
        return _err(f"schema type '{type_}' has an empty/invalid body in schema.yaml")
    valid_subs = type_spec.get("subtypes", [])
    if subtype not in valid_subs:
        return _err(f"unknown subtype '{subtype}' for type '{type_}' "
                    f"(valid: {valid_subs})")

    # --- check for existing entity (never clobber) ---
    entity_dir = vpath / "entities" / type_
    entity_path = entity_dir / f"{slug}.md"
    if entity_path.exists():
        return _err(f"entity '{slug}' already exists at {entity_path.relative_to(vpath)}")

    # --- build frontmatter dict ---
    data = data or {}
    from datetime import date
    fm: dict[str, Any] = {
        "slug": slug,
        "type": type_,
        "subtype": subtype,
        "title": data.get("title") or slug.replace("-", " ").title(),
        "status": data.get("status", "stub"),
        "confidence": data.get("confidence", 1.0),
        "created": data.get("created") or date.today().isoformat(),
        "sources": data.get("sources", []),
        "sources_hash": data.get("sources_hash") or f"manual-create-{slug}",
    }

    # Merge in any additional fields from data that aren't already set.
    # Skip the ones we've already populated.
    skip = {"slug", "type", "subtype", "title", "status", "confidence",
            "created", "sources", "sources_hash"}
    for k, v in data.items():
        if k not in skip and v is not None:
            fm[k] = v

    # --- render entity file (reuse ingest.py's canonical writers) ---
    from agent_vault import ingest as _ingest
    fm_text = _ingest._dump_frontmatter(fm)  # type: ignore[no-untyped-call]
    related = fm.get("related") or []
    links_block = _ingest._render_link_block(related)  # type: ignore[no-untyped-call]
    parts = [f"---\n{fm_text}---\n\n{links_block}"]
    if prose:
        parts.append(f"\n{prose.rstrip()}\n")
    else:
        parts.append("\n")
    entity_text = "".join(parts)

    # --- write atomically ---
    try:
        entity_dir.mkdir(parents=True, exist_ok=True)
        tmp = str(entity_path) + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            fh.write(entity_text)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, entity_path)
    except OSError as e:
        return _err(f"could not write entity: {e}")

    # --- rebuild _index.json so the entity is immediately findable ---
    try:
        from agent_vault import build_index
        build_index.reindex(str(vpath), quiet=True)  # type: ignore[no-untyped-call]
    except Exception:  # noqa: BLE001 - index rebuild is best-effort
        pass  # entity was written; next cadence will rebuild the index

    rel = f"entities/{type_}/{slug}.md"
    return {"ok": True, "slug": slug, "path": rel}


def _append_submission(vault: Path, actor: str, path: str, sha: str, nbytes: int) -> None:
    """Append-only attribution log for agent contributions. Best-effort."""
    try:
        disc = vault / "discovery"
        disc.mkdir(exist_ok=True)
        rec = {
            "ts": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "actor": actor or "unknown",
            "path": path,
            "sha256": sha,
            "bytes": nbytes,
        }
        with open(disc / "_submissions.jsonl", "a", encoding="utf-8") as fh:
            fh.write(json.dumps(rec) + "\n")
    except OSError:
        pass


def resolve_credential(vault: str, slug: str) -> dict[str, Any]:
    """Resolve an entity's `credential_ref` to its plaintext secret.

    Disabled unless AGENT_VAULT_MCP_ALLOW_RESOLVE is set (a shared multi-agent
    server should not hand out secrets by default). The secret is never logged.
    Returns {ok, secret} or {error}.
    """
    if os.environ.get(_RESOLVE_ENV, "").strip().lower() not in ("1", "true", "yes"):
        return _err(f"credential resolution is disabled (set {_RESOLVE_ENV}=1 to enable)")
    if not _SLUG_RE.match(slug or ""):
        return _err("invalid slug format")
    vpath = Path(vault)
    entity = next((e for e in _reads.load_index(vpath) if e.get("slug") == slug), None)
    if not entity or not entity.get("path"):
        return _err("entity not found")
    epath = vpath / entity["path"]
    if not epath.exists():
        return _err("entity file not found")
    ref = _reads.read_credential_ref(epath)
    if not ref:
        return _err("credential_ref not found or unreadable")
    try:
        from agent_vault.resolvers import parse_ref, resolve as _resolve
        parse_ref(ref)  # type: ignore[no-untyped-call]  # legacy untyped resolver fn
        secret = _resolve(ref, str(vpath))  # type: ignore[no-untyped-call]  # legacy untyped resolver fn
        return {"ok": True, "secret": secret}
    except ImportError as e:
        return _err(f"resolver package unavailable: {e}")
    except Exception as e:  # noqa: BLE001 - never echo the secret in an error
        name = type(e).__name__
        if "ResolverError" in name:
            return _err(str(e))
        return _err("credential resolution failed")


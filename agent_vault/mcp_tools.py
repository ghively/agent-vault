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


def search(vault: str, query: str, limit: int = 20) -> dict[str, Any]:
    """Ranked full-text search over entity prose AND metadata.

    Returns {query, hits:[{slug,title,type,subtype,status,path,score,snippet}],
    fts}. `fts` is True when the prose-aware bm25 path is live (vs the metadata
    fallback). This is the primary way an agent recalls knowledge from the wiki.
    """
    q = (query or "").strip()
    if not q:
        return {"query": "", "hits": [], "fts": _search.fts5_available()}
    hits = _search.search(vault, q, limit)
    return {"query": q, "hits": hits, "fts": _search.fts5_available()}


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
        return _reads.build_entity_detail(vpath, slug, epath)
    except OSError as e:
        return _err(f"could not read entity: {e}")


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

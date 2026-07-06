"""Credential resolve and entity recompile endpoints.

POST /api/creds/{slug}/resolve — Resolve an entity's credential_ref to its secret.
POST /api/entities/{slug}/recompile — Trigger a compile for one entity.

Both endpoints mirror SynapseNAS shapes but run in-process (resolve calls
resolver functions directly; recompile acquires vault-wide write lock).
"""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any

import yaml
from fastapi import APIRouter, Depends, HTTPException, Request

from agent_vault.api.config import Settings
from agent_vault.resolvers import parse_ref, resolve
from agent_vault import compiler

router = APIRouter()

# Slug validation regex (from SynapseNAS server/actions.py)
_SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,127}$")


def _valid_slug(s: str) -> bool:
    """Validate slug format (lowercase alphanumeric, limited special chars)."""
    return bool(isinstance(s, str) and _SLUG_RE.match(s))


async def get_settings(request: Request) -> Settings:
    """Get settings, with resolved vault_path from request state (MTAV)."""
    s = request.app.state.settings  # type: ignore
    vault_path = getattr(request.state, "vault_path", "") or ""
    if vault_path and vault_path != s.vault_path:
        from dataclasses import replace
        return replace(s, vault_path=vault_path)
    return s


def _load_index(vault: Path) -> list[dict[str, Any]]:
    """Load _index.json from vault."""
    import json

    p = vault / "_index.json"
    if not p.exists():
        return []
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return []
    ents = data.get("entities", []) if isinstance(data, dict) else []
    return ents if isinstance(ents, list) else []


def _read_credential_ref(path: Path) -> str | None:
    """Extract credential_ref from entity file."""
    _CRED_RE = re.compile(r"credential_ref:\s*(\S+)")
    m = _CRED_RE.search(path.read_text(encoding="utf-8"))
    return m.group(1) if m else None


@router.post("/creds/{slug}/resolve")
async def resolve_credential(
    slug: str,
    request: Request,
    s: Settings = Depends(get_settings),
) -> dict[str, Any]:
    """Resolve an entity's credential_ref to its actual secret.

    The secret is returned in the response body and is NEVER logged
    (no print, no logger, no error body echoing it).

    Args:
        slug: Entity slug to resolve credential for
        s: Settings (dependency injected)

    Returns:
        Dict with {"ok": true, "secret": "<plaintext>"}

    Raises:
        HTTPException: 422 for invalid slug, 404 for entity not found,
                      502 for resolve failure
    """
    if not _valid_slug(slug):
        raise HTTPException(status_code=422, detail="invalid slug format")

    vault = Path(s.vault_path)

    # Find entity by slug
    entities = _load_index(vault)
    entity = next((e for e in entities if e.get("slug") == slug), None)
    if not entity or not entity.get("path"):
        raise HTTPException(status_code=404, detail="entity not found")

    # Read credential_ref from entity file
    entity_path = vault / entity["path"]
    if not entity_path.exists():
        raise HTTPException(status_code=404, detail="entity file not found")

    ref = _read_credential_ref(entity_path)
    if not ref:
        raise HTTPException(status_code=502, detail="credential_ref not found or unreadable")

    try:
        # Parse the ref first (validation)
        _ = parse_ref(ref)  # type: ignore[no-untyped-call]  # legacy untyped resolver fn

        # Resolve the ref to get the actual secret
        secret = resolve(ref, str(vault))  # type: ignore[no-untyped-call]  # legacy untyped resolver fn

        # Return the secret - never log it
        return {"ok": True, "secret": secret}

    except ImportError as e:
        raise HTTPException(status_code=502, detail=f"resolver package unavailable: {e}")
    except Exception as e:
        # Check if it's a ResolverError (safe to show message)
        error_msg = str(e)
        if "ResolverError" in type(e).__name__:
            # ResolverError messages are safe (they never contain the secret)
            raise HTTPException(status_code=502, detail=error_msg)
        # Catch-all for any other error - never echo the secret
        raise HTTPException(status_code=502, detail="credential resolution failed")


@router.post("/entities/{slug}/recompile")
async def recompile_entity(
    slug: str,
    request: Request,
    s: Settings = Depends(get_settings),
) -> dict[str, Any]:
    """Trigger a compile for a single entity under the vault-wide write lock.

    This endpoint mirrors the SynapseNAS POST /api/entities/{slug}/recompile
    shape. It acquires the vault-wide write lock for the duration of the compile.

    Args:
        slug: Entity slug to recompile
        s: Settings (dependency injected)

    Returns:
        Dict with compile result summary

    Raises:
        HTTPException: 422 for invalid slug, 404 for entity not found
    """
    if not _valid_slug(slug):
        raise HTTPException(status_code=422, detail="invalid slug format")

    vault = Path(s.vault_path)

    # Find entity by slug
    entities = _load_index(vault)
    entity = next((e for e in entities if e.get("slug") == slug), None)
    if not entity or not entity.get("path"):
        raise HTTPException(status_code=404, detail="entity not found")

    entity_path = vault / entity["path"]
    if not entity_path.exists():
        raise HTTPException(status_code=404, detail="entity file not found")

    try:
        # Acquire vault-wide write lock for the compile
        from agent_vault.locking import vault_lock, LockTimeout

        with vault_lock(str(vault)):
            # Read the entity file to get its current state
            raw = entity_path.read_text(encoding="utf-8")

            # Parse frontmatter to check if it needs compile
            _FM_RE = re.compile(r"^---\n(.*?)\n---\n(.*)$", re.S)
            m = _FM_RE.match(raw)
            if not m:
                raise HTTPException(status_code=422, detail="malformed entity file")

            fm_text = m.group(1)
            try:
                _ = yaml.safe_load(fm_text) or {}
            except yaml.YAMLError:
                raise HTTPException(status_code=422, detail="malformed frontmatter")

            # Set status to stub to make it eligible for compile (mirror NAS behavior)
            _STATUS_RE = re.compile(r"^(status:[ \t]*)\S+[^\n]*$", re.M)
            new_fm = _STATUS_RE.sub(r"\g<1>stub", fm_text, count=1)

            # Write back with stub status
            tmp = str(entity_path) + ".tmp"
            out = f"---\n{new_fm}\n---\n\n{m.group(2)}"
            Path(tmp).write_text(out, encoding="utf-8")
            os.replace(tmp, str(entity_path))

            # Run compile for just this entity. We are ALREADY holding the
            # vault lock, and flock is NOT re-entrant across two open()s in one
            # process — so calling the public compiler.compile_all() here (which
            # re-acquires the lock) would self-deadlock until the lock timeout
            # (default 600s) and then 503. Call the lock-free primitive instead;
            # the status flip above and this compile stay atomic under the one
            # lock we hold. Mirrors edit.py's "mutate under the lock, delegate to
            # the lock-free path" pattern. See docs/AUDIT.md D1.
            result: dict[str, Any] = compiler._compile_all_locked(str(vault), only=slug)  # type: ignore[no-untyped-call]  # legacy untyped compile fn

            return result

    except LockTimeout:
        raise HTTPException(status_code=503, detail="vault is locked by another operation")
    except HTTPException:
        # The 422s raised above (malformed entity file / frontmatter) are
        # Exception subclasses; let them propagate with their own status
        # instead of being rewrapped as a generic 502 by the catch-all below.
        raise
    except ImportError as e:
        raise HTTPException(status_code=502, detail=f"compiler module unavailable: {e}")
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"compile failed: {str(e)}")

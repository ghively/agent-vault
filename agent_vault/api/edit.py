"""Human edit endpoints: schema picker, reclassify, frontmatter patch, raw editor.

GET   /api/schema                        — vault taxonomy (types/subtypes/tags) for UI pickers
POST  /api/entities/{slug}/reclassify    — human-driven re-filing of one entity
PATCH /api/entities/{slug}               — edit title/tags/notes frontmatter fields only
GET   /api/entities/{slug}/raw           — full raw markdown of the entity file
PUT   /api/entities/{slug}/raw           — replace the entity file (in-app raw editor)

Security posture mirrors review.py: imports ONLY from the trusted agent_vault
package (never from the vault DATA dir), validates slugs against the same
conservative regex creds.py uses, secret-scans every new frontmatter value the
way validate.py does, and takes the vault-wide write lock around every mutation.

Locking note: reclassify_apply.apply_all takes the vault lock ITSELF, and the
flock behind vault_lock is NOT re-entrant within one process (a second open()
of the lock file conflicts with the first) — so this module appends its ledger
records under the lock, releases it, and then delegates to apply_all, exactly
the way review.cmd_approve does for kind == "reclassify".
"""

from __future__ import annotations

import datetime
import os
import re
from pathlib import Path
from typing import Any

import yaml
from fastapi import APIRouter, Body, Depends, HTTPException, Request
from pydantic import BaseModel

from agent_vault.api.config import Settings
from agent_vault.api import reads

# Import ONLY from the trusted agent_vault package (see module docstring).
from agent_vault import ingest
from agent_vault import reclassify_apply
from agent_vault import review as review_module
from agent_vault import secret_scan
from agent_vault import validate as validate_module
from agent_vault.locking import vault_lock, LockTimeout

router = APIRouter()

# Same conservative slug pattern as creds.py / jobs.py (SynapseNAS heritage).
_SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,127}$")
# Frontmatter shape used across the pipeline (reclassify_apply/review/validate).
_PARSE_RE = re.compile(r"^---\n(.*?)\n---\n(.*)$", re.S)

_MAX_RAW_BYTES = 1024 * 1024  # 1MB cap for PUT raw
_MAX_TITLE_LEN = 300

PATCHABLE_FIELDS = ("title", "tags", "notes")


async def get_settings(request: Request) -> Settings:
    """Get settings from app state (dependency injection)."""
    return request.app.state.settings  # type: ignore


def _valid_slug(s: str) -> bool:
    """Validate slug format (lowercase alphanumeric, limited special chars)."""
    return bool(isinstance(s, str) and _SLUG_RE.match(s))


def _find_entity(vault: Path, slug: str) -> Path | None:
    """Locate entities/<type>/<slug>.md. Ambiguity (same slug under two type
    dirs) is an integrity bug — refuse rather than first-wins (mirrors
    reclassify_apply.find_entity_by_slug)."""
    matches = sorted((vault / "entities").glob(f"*/{slug}.md"))
    if len(matches) > 1:
        types = ", ".join(p.parent.name for p in matches)
        raise HTTPException(
            status_code=409,
            detail=f"slug {slug!r} exists under multiple type dirs ({types}); "
                   f"resolve by hand first",
        )
    return matches[0] if matches else None


def _require_entity(vault: Path, slug: str) -> Path:
    """422 on malformed slug, 404 when no entities/*/<slug>.md exists."""
    if not _valid_slug(slug):
        raise HTTPException(status_code=422, detail="invalid slug format")
    path = _find_entity(vault, slug)
    if path is None:
        raise HTTPException(status_code=404, detail="entity not found")
    return path


def _load_schema(vault: Path) -> dict[str, Any]:
    """Load registry/schema.yaml, tolerating absence/corruption ({})."""
    p = vault / "registry" / "schema.yaml"
    if not p.exists():
        return {}
    try:
        data = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError):
        return {}
    return data if isinstance(data, dict) else {}


def _schema_types(schema: dict[str, Any]) -> dict[str, list[str]]:
    """types -> sorted subtypes, tolerating empty/None type bodies."""
    out: dict[str, list[str]] = {}
    types = schema.get("types") or {}
    if not isinstance(types, dict):
        return out
    for t, spec in types.items():
        subs = spec.get("subtypes") if isinstance(spec, dict) else None
        out[str(t)] = sorted(str(x) for x in (subs or []))
    return out


def _schema_tags(schema: dict[str, Any]) -> list[str]:
    tags = schema.get("tags") or {}
    if isinstance(tags, dict):
        return sorted(str(k) for k in tags)
    if isinstance(tags, list):
        return sorted(str(x) for x in tags)
    return []


def _parse_frontmatter(raw: str) -> tuple[str, dict[str, Any], str] | None:
    """Split raw file text into (fm_text, fm_dict, body); None if unparseable."""
    m = _PARSE_RE.match(raw)
    if not m:
        return None
    try:
        fm = yaml.safe_load(m.group(1)) or {}
    except yaml.YAMLError:
        return None
    if not isinstance(fm, dict):
        return None
    return m.group(1), fm, m.group(2)


def _atomic_write(path: Path, text: str) -> None:
    """tmp + fsync + os.replace — same posture as every pipeline writer."""
    tmp = str(path) + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(text)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, str(path))


def _secret_in_frontmatter(fm: dict[str, Any], fm_text: str) -> str | None:
    """Strict secret scan over frontmatter values + comment lines, the same
    way validate.validate_file does (credential_ref is a reference — exempt).
    Returns a human-readable finding, or None when clean."""
    for k, v in fm.items():
        if k == "credential_ref":
            continue
        for sval in validate_module._flatten_strings(v):  # type: ignore[no-untyped-call]
            kind = secret_scan.looks_like_secret(sval)  # type: ignore[no-untyped-call]
            if kind:
                return (f"frontmatter field '{k}' looks like a plaintext secret "
                        f"({kind}); reference it via credential_ref, never store "
                        f"the secret")
    for line in fm_text.splitlines():
        ls = line.strip()
        if not ls.startswith("#"):
            continue
        kind = secret_scan.looks_like_secret(ls)  # type: ignore[no-untyped-call]
        if kind:
            return (f"frontmatter comment line looks like a plaintext secret "
                    f"({kind}); never write a secret into the file")
    return None


def _validate_or_rollback(vault: Path, path: Path, original: str) -> None:
    """Run validate.validate_file on the just-written file; on errors restore
    the original bytes and raise 422 carrying the validator's errors."""
    schema = _load_schema(vault)
    errs, _warns = validate_module.validate_file(  # type: ignore[no-untyped-call]
        str(path), schema, str(vault)
    )
    if errs:
        _atomic_write(path, original)
        raise HTTPException(
            status_code=422,
            detail={"message": "validation failed; change rolled back",
                    "errors": [str(e) for e in errs]},
        )


def _yaml_scalar(value: str) -> str:
    """Emit a YAML scalar with ingest's quoting discipline (round-trip-safe)."""
    return str(ingest._yaml_str(value))  # type: ignore[no-untyped-call]


def _set_scalar_line(fm_text: str, key: str, value: str) -> str:
    """Replace (or append) a single `key: value` frontmatter line. Everything
    else in the frontmatter is untouched byte-for-byte."""
    rendered = f"{key}: {_yaml_scalar(value)}"
    pattern = re.compile(rf"^{re.escape(key)}:[ \t]*[^\n]*$", re.M)
    if pattern.search(fm_text):
        # Function replacement: the rendered value may contain backslashes
        # (JSON quoting) that a template replacement would misinterpret.
        return pattern.sub(lambda _m: rendered, fm_text, count=1)
    return fm_text.rstrip("\n") + "\n" + rendered


def _set_tags_lines(fm_text: str, tags: list[str]) -> str:
    """Replace the `tags:` field (flow or block style) with a flow-style list,
    leaving every other frontmatter line untouched."""
    rendered = "tags: [" + ", ".join(_yaml_scalar(t) for t in tags) + "]"
    lines = fm_text.split("\n")
    for i, line in enumerate(lines):
        if not re.match(r"^tags:", line):
            continue
        after = line[len("tags:"):].strip()
        if after and not after.startswith("#"):
            # Flow style (or scalar) on one line: replace the line.
            lines[i] = rendered
            return "\n".join(lines)
        # Block style: consume the indented `- item` lines that follow.
        j = i + 1
        while j < len(lines) and re.match(r"^[ \t]+-[ \t]", lines[j]):
            j += 1
        return "\n".join(lines[:i] + [rendered] + lines[j:])
    return fm_text.rstrip("\n") + "\n" + rendered


def _now() -> str:
    return datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")


# ============================================================================
# GET /api/schema
# ============================================================================
@router.get("/schema")
def get_schema(
    s: Settings = Depends(get_settings),
) -> dict[str, Any]:
    """Vault taxonomy for UI pickers: every type (incl. `unknown`) with its
    subtypes, plus the flat tag vocabulary. Sorted, deterministic."""
    schema = _load_schema(Path(s.vault_path))
    types = _schema_types(schema)
    return {
        "types": [{"type": t, "subtypes": types[t]} for t in sorted(types)],
        "tags": _schema_tags(schema),
    }


# ============================================================================
# POST /api/entities/{slug}/reclassify
# ============================================================================
class ReclassifyRequest(BaseModel):
    """Target classification for a human-driven reclassify."""
    to_type: str
    to_subtype: str
    reason: str = ""


@router.post("/entities/{slug}/reclassify")
def reclassify_entity(
    slug: str,
    body: ReclassifyRequest,
    s: Settings = Depends(get_settings),
) -> dict[str, Any]:
    """Re-file one entity under a new (type, subtype), human-driven.

    Appends a `queue_for_review` reclassify record (shaped like promote.py's
    ledger records, provenance-marked human_review) plus a `review_approved`
    record (the way review.cmd_approve does), then delegates the actual move/
    frontmatter-rewrite/ref-rewrite to reclassify_apply.apply_all — the same
    two-phase-commit apply step the CLI uses. apply_all takes the vault lock
    itself and rebuilds _index.json when anything was applied.

    Returns 400 for an invalid or unchanged target, 404 for an unknown slug,
    409 when the apply step reports the reclassify could not be applied.
    """
    vault = Path(s.vault_path)
    path = _require_entity(vault, slug)

    parsed = _parse_frontmatter(path.read_text(encoding="utf-8"))
    if parsed is None:
        raise HTTPException(status_code=422, detail="entity file has no parseable frontmatter")
    _fm_text, fm, _body_text = parsed
    cur_type = str(fm.get("type") or path.parent.name)
    cur_subtype = str(fm.get("subtype") or "")

    to_type = body.to_type.strip()
    to_subtype = body.to_subtype.strip()
    types = _schema_types(_load_schema(vault))
    if to_type not in types:
        raise HTTPException(
            status_code=400,
            detail=f"target type {to_type!r} not in registry "
                   f"(valid types: {sorted(types)})",
        )
    if to_subtype not in types[to_type]:
        raise HTTPException(
            status_code=400,
            detail=f"target subtype {to_subtype!r} not valid for type {to_type!r} "
                   f"(valid: {types[to_type]})",
        )
    if (to_type, to_subtype) == (cur_type, cur_subtype):
        raise HTTPException(
            status_code=400,
            detail=f"entity already classified {cur_type}/{cur_subtype}",
        )

    # Ledger records: identity/proposal shaped exactly like promote.py's
    # reclassify queue records (proposal_identity/log_action), so
    # reclassify_apply.queued_reclassifies picks them up unchanged.
    identity = ["reclassify", slug, to_type, to_subtype]
    reason = body.reason.strip()
    proposal = {
        "kind": "reclassify",
        "from_entity": slug,
        "to_type": to_type,
        "to_subtype": to_subtype,
        "reason": reason,
        "proposed_by": "human_review",
    }
    now = _now()
    queue_rec = {
        "ts": now,
        "identity": identity,
        "kind": "reclassify",
        "action": "queue_for_review",
        "reason": reason or "human-initiated reclassify via API",
        "sightings": 1,
        "distinct": 1,
        "max_confidence": 1.0,
        "from_entities": [slug],
        "models": ["human_review"],
        "evidence_count": 0,
        "proposal": proposal,
        "origin": "human_review",
    }
    approved_rec = {
        "ts": now,
        "identity": identity,
        "kind": "reclassify",
        "action": "review_approved",
        "reason": reason or "approved via API reclassify",
        "sightings": 1,
        "distinct": 1,
        "proposal": proposal,
        "by": "api.edit",
    }

    try:
        # Append the audit records under the vault lock, then RELEASE it:
        # apply_all takes the lock itself and flock is not re-entrant in-process
        # (see module docstring — same posture as review.cmd_approve).
        with vault_lock(str(vault)):
            review_module.append_log(str(vault), queue_rec)  # type: ignore[no-untyped-call]
            review_module.append_log(str(vault), approved_rec)  # type: ignore[no-untyped-call]
        results = reclassify_apply.apply_all(  # type: ignore[no-untyped-call]
            str(vault), only_slug=slug
        )
    except LockTimeout:
        raise HTTPException(status_code=503, detail="vault is locked by another operation")

    ours = [r for r in results if r.get("slug") == slug]
    applied = next(
        (r for r in ours
         if r.get("status") == "applied" and r.get("to") == f"{to_type}/{slug}"),
        None,
    )
    if applied is None:
        reasons = [str(r.get("reason", "")) for r in ours if r.get("status") != "applied"]
        raise HTTPException(
            status_code=409,
            detail="; ".join(x for x in reasons if x) or "reclassify was not applied",
        )

    refs = int(applied.get("refs_updated", 0))
    return {
        "slug": slug,
        "from": f"{cur_type}/{cur_subtype}",
        "to": f"{to_type}/{to_subtype}",
        "moved": bool(applied.get("moves_file")),
        "refs_rewritten": refs,
        "detail": f"reclassified {cur_type}/{cur_subtype} -> {to_type}/{to_subtype}; "
                  f"{refs} cross-ref(s) rewritten",
    }


# ============================================================================
# PATCH /api/entities/{slug}
# ============================================================================
@router.patch("/entities/{slug}")
def patch_entity(
    slug: str,
    payload: dict[str, Any] = Body(...),
    s: Settings = Depends(get_settings),
) -> dict[str, Any]:
    """Edit ONLY the `title` / `tags` / `notes` frontmatter fields.

    Line-targeted rewrite: every byte outside the changed frontmatter lines is
    preserved. New values use ingest's YAML quoting discipline, are secret-
    scanned before anything is written, and the resulting file must pass
    validate.validate_file — otherwise the original content is restored.
    """
    vault = Path(s.vault_path)
    path = _require_entity(vault, slug)

    if not payload:
        raise HTTPException(status_code=400, detail="empty body — nothing to edit")
    unknown = sorted(set(payload) - set(PATCHABLE_FIELDS))
    if unknown:
        raise HTTPException(
            status_code=400,
            detail=f"unknown field(s) {unknown}; editable fields: {list(PATCHABLE_FIELDS)}",
        )

    schema = _load_schema(vault)
    new_values: list[str] = []

    title = payload.get("title")
    if "title" in payload:
        if not isinstance(title, str) or not title.strip():
            raise HTTPException(status_code=400, detail="title must be a non-empty string")
        if "\n" in title or "\r" in title:
            raise HTTPException(status_code=400, detail="title must be a single line")
        if len(title) > _MAX_TITLE_LEN:
            raise HTTPException(
                status_code=400, detail=f"title too long (max {_MAX_TITLE_LEN} chars)"
            )
        new_values.append(title)

    tags = payload.get("tags")
    if "tags" in payload:
        if not isinstance(tags, list) or not all(isinstance(t, str) for t in tags):
            raise HTTPException(status_code=400, detail="tags must be a list of strings")
        valid_tags = set(_schema_tags(schema))
        bad = sorted(t for t in tags if t not in valid_tags)
        if bad:
            raise HTTPException(
                status_code=400,
                detail=f"unknown tag(s) {bad} — promote them first or add to "
                       f"schema.yaml (valid tags: {sorted(valid_tags)})",
            )
        new_values.extend(tags)

    notes = payload.get("notes")
    if "notes" in payload:
        if not isinstance(notes, str):
            raise HTTPException(status_code=400, detail="notes must be a string")
        new_values.append(notes)

    # Secret guard BEFORE anything touches disk (validate.py's strict tier).
    for v in new_values:
        kind = secret_scan.looks_like_secret(v)  # type: ignore[no-untyped-call]
        if kind:
            raise HTTPException(
                status_code=422,
                detail=f"value looks like a plaintext secret ({kind}); "
                       f"reference it via credential_ref instead — nothing was written",
            )

    try:
        with vault_lock(str(vault)):
            original = path.read_text(encoding="utf-8")
            m = _PARSE_RE.match(original)
            if not m:
                raise HTTPException(
                    status_code=422, detail="entity file has no parseable frontmatter"
                )
            fm_text, body_text = m.group(1), m.group(2)
            if "title" in payload and isinstance(title, str):
                fm_text = _set_scalar_line(fm_text, "title", title)
            if "tags" in payload and isinstance(tags, list):
                fm_text = _set_tags_lines(fm_text, [str(t) for t in tags])
            if "notes" in payload and isinstance(notes, str):
                fm_text = _set_scalar_line(fm_text, "notes", notes)

            _atomic_write(path, f"---\n{fm_text}\n---\n{body_text}")
            _validate_or_rollback(vault, path, original)

            # Keep the search index current (same as the review endpoints).
            ingest.refresh_index(str(vault))  # type: ignore[no-untyped-call]
    except LockTimeout:
        raise HTTPException(status_code=503, detail="vault is locked by another operation")

    return reads.build_entity_detail(vault, slug, path)


# ============================================================================
# GET/PUT /api/entities/{slug}/raw
# ============================================================================
class RawPutRequest(BaseModel):
    """Full replacement content for the entity file."""
    content: str


@router.get("/entities/{slug}/raw")
def get_entity_raw(
    slug: str,
    s: Settings = Depends(get_settings),
) -> dict[str, Any]:
    """Raw markdown of the entity file (for the in-app editor)."""
    vault = Path(s.vault_path)
    path = _require_entity(vault, slug)
    return {
        "path": path.relative_to(vault).as_posix(),
        "content": path.read_text(encoding="utf-8"),
    }


@router.put("/entities/{slug}/raw")
def put_entity_raw(
    slug: str,
    body: RawPutRequest,
    s: Settings = Depends(get_settings),
) -> dict[str, Any]:
    """Replace the entire entity file from the in-app raw editor.

    The frontmatter's slug must match the filename and its type must match the
    directory — renames/moves are the reclassify endpoint's job. Content is
    secret-scanned, written atomically under the vault lock, and validated;
    a validation failure restores the original bytes and returns 422.
    """
    vault = Path(s.vault_path)
    path = _require_entity(vault, slug)

    content = body.content
    if not content.strip():
        raise HTTPException(status_code=400, detail="content is empty")
    if len(content.encode("utf-8")) > _MAX_RAW_BYTES:
        raise HTTPException(status_code=400, detail="content exceeds 1MB limit")
    parsed = _parse_frontmatter(content)
    if parsed is None:
        raise HTTPException(
            status_code=400,
            detail="content must be a frontmatter+body markdown file "
                   "(---\\n<yaml>\\n---\\n<body>) with valid YAML frontmatter",
        )
    fm_text, fm, _body_text = parsed

    fm_slug = str(fm.get("slug") or "")
    if fm_slug != slug:
        raise HTTPException(
            status_code=400,
            detail=f"frontmatter slug {fm_slug!r} must match the filename slug "
                   f"{slug!r} (renames are not allowed in the raw editor)",
        )
    dir_type = path.parent.name
    fm_type = str(fm.get("type") or "")
    if fm_type != dir_type:
        raise HTTPException(
            status_code=400,
            detail=f"frontmatter type {fm_type!r} must match the entity's directory "
                   f"{dir_type!r} — use the reclassify endpoint to move an entity",
        )

    finding = _secret_in_frontmatter(fm, fm_text)
    if finding:
        raise HTTPException(status_code=422, detail=f"{finding} — nothing was written")

    try:
        with vault_lock(str(vault)):
            original = path.read_text(encoding="utf-8")
            _atomic_write(path, content)
            _validate_or_rollback(vault, path, original)
            ingest.refresh_index(str(vault))  # type: ignore[no-untyped-call]
    except LockTimeout:
        raise HTTPException(status_code=503, detail="vault is locked by another operation")

    return {"ok": True, "path": path.relative_to(vault).as_posix()}

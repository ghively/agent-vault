"""History endpoints for Agent Vault API.

Reads vault run history (cadence executions) and ledger counts
(proposals/promoted/manifest entries). Mirrors SynapseNAS server/vault.py
shapes for Phase 3/4 compatibility.
"""

from __future__ import annotations

import json
from pathlib import Path

from fastapi import APIRouter, Depends, Query, Request

from agent_vault.api.config import Settings

router = APIRouter()


async def get_settings(request: Request) -> Settings:
    """Get settings from app state (dependency injection)."""
    return request.app.state.settings  # type: ignore


def read_jsonl(path: Path) -> list[dict[str, object]]:
    """Read a JSONL file and return list of dicts.

    Gracefully degrades to empty list if file doesn't exist or has
    JSON decode errors (matches SynapseNAS vault.py behavior).
    """
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


@router.get("/runs")
def get_runs(
    limit: int = Query(50, ge=0, le=1000),
    s: Settings = Depends(get_settings),
) -> dict[str, object]:
    """Get pipeline run history (ingest/compile/promote cadence runs).

    Returns newest-first limited list. Matches SynapseNAS GET /api/runs shape.
    """
    vault_path = Path(s.vault_path)

    runs_path = vault_path / "discovery" / "_runs.jsonl"
    if not runs_path.exists():
        return {"runs": []}

    runs = read_jsonl(runs_path)
    runs.reverse()  # newest first
    # `limit` is validated ge=0; slice unconditionally so limit=0 means "none",
    # not "all" (the old `if limit` treated 0 as falsy and returned everything).
    limited_runs = runs[:limit]

    return {"runs": limited_runs}


@router.get("/ledgers")
def get_ledgers(s: Settings = Depends(get_settings)) -> dict[str, object]:
    """Get append-only ledger entry counts.

    Returns counts for proposals.jsonl, promoted.jsonl, and
    _manifest.jsonl. Matches SynapseNAS GET /api/ledgers shape.
    """
    vault_path = Path(s.vault_path)

    discovery_dir = vault_path / "discovery"
    proposals = read_jsonl(discovery_dir / "proposals.jsonl")
    promoted = read_jsonl(discovery_dir / "promoted.jsonl")
    manifest = read_jsonl(vault_path / "raw" / "_manifest.jsonl")

    return {
        "proposals": len(proposals),
        "promoted": len(promoted),
        "manifest": len(manifest),
    }

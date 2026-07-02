"""GET /api/settings — surface the vault's configuration (read-only overview).

Gathers the service config (host/port/path/auth), vault content stats (entity
count, index status), the compiler's prompt-contract version + configured model,
the configured credential-resolver backends, and the cadence scripts. The UI
preferences (theme/density/scale) are client-side and not included here.
"""

from __future__ import annotations

import json
import os
from importlib.metadata import version as _pkg_version
from pathlib import Path
from typing import Any

import yaml
from fastapi import APIRouter, Depends, Request

from agent_vault.api.config import Settings

router = APIRouter()


async def get_settings(request: Request) -> Settings:
    """Settings from app state (dependency injection)."""
    return request.app.state.settings  # type: ignore[no-any-return]


def _entity_count(vault: Path) -> int:
    p = vault / "_index.json"
    if not p.exists():
        return 0
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        ents = data.get("entities", []) if isinstance(data, dict) else []
        return len(ents) if isinstance(ents, list) else 0
    except (json.JSONDecodeError, OSError):
        return 0


def _resolver_backends(vault: Path) -> tuple[str | None, list[dict[str, str]]]:
    p = vault / "registry" / "resolvers.yaml"
    if not p.exists():
        return None, []
    try:
        data = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError:
        return None, []
    if not isinstance(data, dict):
        return None, []
    default = data.get("default_resolver") if isinstance(data.get("default_resolver"), str) else None
    backends: list[dict[str, str]] = []
    resolvers = data.get("resolvers")
    if isinstance(resolvers, dict):
        for scheme, cfg in resolvers.items():
            if isinstance(cfg, dict):
                backends.append(
                    {
                        "scheme": str(scheme),
                        "module": str(cfg.get("module", "")),
                        "description": str(cfg.get("description", "")).split("\n")[0],
                    }
                )
    return default, backends


@router.get("/settings")
async def get_settings_overview(s: Settings = Depends(get_settings)) -> dict[str, Any]:
    """Return the vault's configuration overview."""
    vault = Path(s.vault_path)
    try:
        pkg_version = _pkg_version("agent-vault")
    except Exception:
        pkg_version = "0.1.0"

    prompt_contract_version = "unknown"
    try:
        from agent_vault.compiler import PROMPT_CONTRACT_VERSION

        prompt_contract_version = str(PROMPT_CONTRACT_VERSION)
    except Exception:
        pass

    default_resolver, backends = _resolver_backends(vault)
    cadences_dir = vault / "cadences"
    cadences = sorted(f.name for f in cadences_dir.glob("*") if f.is_file()) if cadences_dir.is_dir() else []

    return {
        "service": {
            "version": pkg_version,
            "vault_path": str(vault),
            "host": s.host,
            "port": s.port,
            "auth_enabled": bool(s.token),
        },
        "vault": {
            "entity_count": _entity_count(vault),
            "index_built": (vault / "_index.json").exists(),
        },
        "compiler": {
            "prompt_contract_version": prompt_contract_version,
            "ollama_model": os.environ.get("OLLAMA_MODEL", ""),
        },
        "resolvers": {"default": default_resolver, "backends": backends},
        "cadences": cadences,
    }

"""Runtime configuration endpoints: GET /api/config, POST /api/config/apply.

Surfaces the pipeline-relevant environment knobs (with the real defaults the
pipeline modules use), the promotion thresholds from registry/schema.yaml, and
the configured credential-resolver backends. Applying changes only touches an
allowlisted set of environment variables — jobs spawned via /api/jobs/run
inherit os.environ (see jobs.py:_vault_env), so changes take effect on the
next job. Thresholds are read-only through the API (edit registry/schema.yaml).
"""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any

import yaml
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

from agent_vault.api.config import Settings
from agent_vault.api.settings import _resolver_backends

router = APIRouter()

# Real defaults from the pipeline modules:
#   AGENT_VAULT_COMPILER          compiler.py:get_client       -> "ollama"
#   AGENT_VAULT_PER_SOURCE_CHARS  compiler.py:PER_SOURCE_CHAR_CAP -> "4000"
#   AGENT_VAULT_MAX_RAW_MB        ingest.py:MAX_RAW_BYTES      -> "50"
#   OLLAMA_MODEL                  compiler.py:OllamaClient     -> "qwen2.5:7b-instruct"
ENV_DEFAULTS: dict[str, str] = {
    "AGENT_VAULT_COMPILER": "ollama",
    "AGENT_VAULT_PER_SOURCE_CHARS": "4000",
    "AGENT_VAULT_MAX_RAW_MB": "50",
    "OLLAMA_MODEL": "qwen2.5:7b-instruct",
}

# Valid compiler backends — compiler.py:get_client accepts exactly these.
VALID_COMPILERS = {"ollama", "mock"}

# Sane bounds for the numeric knobs (positive ints).
_INT_BOUNDS: dict[str, tuple[int, int]] = {
    "AGENT_VAULT_PER_SOURCE_CHARS": (1, 1_000_000),
    "AGENT_VAULT_MAX_RAW_MB": (1, 10_000),
}

_MODEL_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/-]{0,127}$")


async def get_settings(request: Request) -> Settings:
    """Get settings, with resolved vault_path from request state (MTAV)."""
    s = request.app.state.settings  # type: ignore
    vault_path = getattr(request.state, "vault_path", "") or ""
    if vault_path and vault_path != s.vault_path:
        from dataclasses import replace
        return replace(s, vault_path=vault_path)
    return s


class ConfigApplyRequest(BaseModel):
    """Body for POST /api/config/apply."""

    env: dict[str, str] | None = None
    thresholds: dict[str, Any] | None = None


def _read_thresholds(vault: Path) -> dict[str, Any]:
    """Promotion thresholds from registry/schema.yaml's `promotion:` block
    (the same block promote.py:decide applies)."""
    out: dict[str, Any] = {"new_tag_min": None, "new_subtype_min": None, "new_type_locked": True}
    p = vault / "registry" / "schema.yaml"
    if not p.exists():
        return out
    try:
        schema = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError):
        return out
    promotion = schema.get("promotion") if isinstance(schema, dict) else None
    if not isinstance(promotion, dict):
        return out

    def _min(rule_name: str) -> int | None:
        rule = promotion.get(rule_name)
        if isinstance(rule, dict) and isinstance(rule.get("min_sightings"), int):
            return int(rule["min_sightings"])
        return None

    out["new_tag_min"] = _min("new_tag")
    out["new_subtype_min"] = _min("new_subtype")
    new_type = promotion.get("new_type")
    if isinstance(new_type, dict):
        out["new_type_locked"] = not bool(new_type.get("auto", False))
    return out


def _config_payload(vault: Path) -> dict[str, Any]:
    """Build the GET /api/config response (fresh read every time)."""
    _default, backends = _resolver_backends(vault)
    return {
        "env": {k: os.environ.get(k, default) for k, default in ENV_DEFAULTS.items()},
        "thresholds": _read_thresholds(vault),
        "resolvers": [
            {"name": b.get("scheme", ""), "detail": b.get("description", "")} for b in backends
        ],
    }


def _validate_env_changes(env: dict[str, str]) -> dict[str, str]:
    """Validate an env-change request against the allowlist and value rules.

    Raises:
        HTTPException: 400 for unknown keys or invalid values.
    """
    unknown = sorted(set(env) - set(ENV_DEFAULTS))
    if unknown:
        raise HTTPException(
            status_code=400,
            detail=f"Unknown env key(s) {unknown}; allowed: {sorted(ENV_DEFAULTS)}",
        )

    validated: dict[str, str] = {}
    for key, value in env.items():
        value = str(value).strip()
        if key == "AGENT_VAULT_COMPILER":
            if value.lower() not in VALID_COMPILERS:
                raise HTTPException(
                    status_code=400,
                    detail=(
                        f"AGENT_VAULT_COMPILER={value!r} invalid; "
                        f"must be one of: {sorted(VALID_COMPILERS)}"
                    ),
                )
            value = value.lower()
        elif key in _INT_BOUNDS:
            lo, hi = _INT_BOUNDS[key]
            if not value.isdigit() or not (lo <= int(value) <= hi):
                raise HTTPException(
                    status_code=400,
                    detail=f"{key}={value!r} invalid; must be an integer in [{lo}, {hi}]",
                )
        elif key == "OLLAMA_MODEL":
            if not _MODEL_RE.match(value):
                raise HTTPException(
                    status_code=400,
                    detail=f"OLLAMA_MODEL={value!r} invalid; must match {_MODEL_RE.pattern}",
                )
        validated[key] = value
    return validated


@router.get("/config")
async def get_config(s: Settings = Depends(get_settings)) -> dict[str, Any]:
    """Current pipeline configuration: env knobs, promotion thresholds,
    resolver backends."""
    return _config_payload(Path(s.vault_path))


@router.post("/config/apply")
async def apply_config(
    body: ConfigApplyRequest,
    s: Settings = Depends(get_settings),
) -> dict[str, Any]:
    """Apply allowlisted env changes (thresholds are read-only via the API).

    Applied keys are written to os.environ, so subsequent /api/jobs/run
    subprocesses inherit them. Returns the same shape as GET /api/config.
    """
    if body.thresholds is not None:
        raise HTTPException(
            status_code=400,
            detail=(
                "thresholds are read-only via the API — edit registry/schema.yaml's "
                "promotion: block instead"
            ),
        )

    if body.env:
        os.environ.update(_validate_env_changes(body.env))

    return _config_payload(Path(s.vault_path))

"""Authentication + per-agent identity and scoping for the Agent Vault API.

Backward-compatible with the original single-token model, extended for a shared
multi-agent deployment (O5/O6):

- **Legacy**: `VAULT_TOKEN=<t>` — one token, full access, actor ``vault-token``.
  Unset ⇒ auth disabled, everything open (actor ``anonymous``, all scopes).
- **Per-agent**: a token registry maps each token to an ``Identity(actor,
  scopes)``. Sources (merged, legacy token wins as admin):
    * `VAULT_TOKENS` env — JSON `{"<token>": {"actor": "...", "scopes": [...]}}`
    * `registry/tokens.yaml` — `tokens: {<token>: {actor, scopes}}` (human-authored)

Scopes gate by request shape, so no per-endpoint wiring is needed:
    read     GET/HEAD on /api/*
    write    POST/PUT/PATCH/DELETE on /api/* (except resolve)
    resolve  POST /api/creds/{slug}/resolve   (returns plaintext secrets)
`admin` implies all. A token missing the required scope gets 403; a
missing/invalid token (when auth is enabled) gets 401. The resolved identity is
stashed on ``request.state.identity`` for attribution/audit downstream.
"""

from __future__ import annotations

import json
import os
import re
import secrets
from dataclasses import dataclass
from typing import Annotated, Callable

from fastapi import HTTPException, Request, Security, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from agent_vault.api.config import Settings

ALL_SCOPES = frozenset({"read", "write", "resolve"})
_RESOLVE_PATH_RE = re.compile(r"^/api/creds/[^/]+/resolve$")


@dataclass(frozen=True)
class Identity:
    """The authenticated caller: who they are and what they may do."""

    actor: str
    scopes: frozenset[str]

    def allows(self, scope: str) -> bool:
        return "admin" in self.scopes or scope in self.scopes


ANONYMOUS = Identity("anonymous", ALL_SCOPES)


def _identity_from_spec(token: str, spec: object) -> Identity:
    """Build an Identity from a registry entry, defaulting conservatively."""
    if not isinstance(spec, dict):
        return Identity(token[:8] or "agent", frozenset({"read"}))
    actor = str(spec.get("actor") or token[:8] or "agent")
    raw_scopes = spec.get("scopes")
    if raw_scopes in (None, "", "all"):
        scopes = ALL_SCOPES
    elif isinstance(raw_scopes, (list, tuple, set)):
        scopes = frozenset(str(s) for s in raw_scopes)
    else:
        scopes = frozenset({str(raw_scopes)})
    return Identity(actor, scopes)


def load_token_registry(settings: Settings) -> dict[str, Identity]:
    """Merge all token sources into {token: Identity}. Never raises."""
    registry: dict[str, Identity] = {}

    # registry/tokens.yaml (human-authored, lowest precedence)
    path = os.path.join(settings.vault_path, "registry", "tokens.yaml")
    if os.path.exists(path):
        try:
            import yaml
            with open(path, encoding="utf-8") as fh:
                data = yaml.safe_load(fh) or {}
            for tok, spec in (data.get("tokens") or {}).items():
                registry[str(tok)] = _identity_from_spec(str(tok), spec)
        except Exception:  # noqa: BLE001 - a bad tokens file must not crash boot
            pass

    # VAULT_TOKENS env JSON
    raw = os.environ.get("VAULT_TOKENS", "").strip()
    if raw:
        try:
            data = json.loads(raw)
            if isinstance(data, dict):
                for tok, spec in data.items():
                    registry[str(tok)] = _identity_from_spec(str(tok), spec)
        except json.JSONDecodeError:
            pass

    # Legacy single token = admin (highest precedence)
    if settings.token:
        registry[settings.token] = Identity("vault-token", ALL_SCOPES)

    return registry


def required_scope(method: str, path: str) -> str | None:
    """The scope a request needs, or None for open paths (health, non-/api)."""
    if not path.startswith("/api/") or path == "/api/health":
        return None
    if method == "POST" and _RESOLVE_PATH_RE.match(path):
        return "resolve"
    if method in ("POST", "PUT", "PATCH", "DELETE"):
        return "write"
    return "read"


def _match_token(presented: str, registry: dict[str, Identity]) -> Identity | None:
    """Constant-time lookup: compare against every token so a match/miss can't
    be told apart by response timing."""
    found: Identity | None = None
    pres = presented.encode("utf-8")
    for tok, ident in registry.items():
        if secrets.compare_digest(pres, tok.encode("utf-8")):
            found = ident
    return found


def create_auth_dependency(settings: Settings) -> Callable[..., None]:
    """Build the FastAPI dependency that authenticates + scope-checks a request
    and records the caller's Identity on ``request.state.identity``."""
    registry = load_token_registry(settings)
    auth_enabled = bool(registry)

    def auth_dependency(
        request: Request,
        credentials: Annotated[
            HTTPAuthorizationCredentials | None,
            Security(HTTPBearer(auto_error=False)),
        ] = None,
    ) -> None:
        if not auth_enabled:
            request.state.identity = ANONYMOUS
            return None

        if credentials is None:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Missing authorization header",
                headers={"WWW-Authenticate": "Bearer"},
            )
        ident = _match_token(credentials.credentials, registry)
        if ident is None:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid authorization token",
                headers={"WWW-Authenticate": "Bearer"},
            )
        scope = required_scope(request.method, request.url.path)
        if scope and not ident.allows(scope):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"token '{ident.actor}' lacks required scope '{scope}'",
            )
        request.state.identity = ident
        return None

    return auth_dependency

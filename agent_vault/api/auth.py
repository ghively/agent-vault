"""Authentication + tenant-aware identity and scoping for the Agent Vault API.

**Backward compatibility** is load-bearing: when ``multi_tenant=False`` (the
default), this module is identical in behaviour to the pre-MTAV auth. The old
``Identity``, ``ANONYMOUS``, ``required_scope``, ``load_token_registry``, and
``create_auth_dependency`` APIs are preserved. New code should use
``create_tenant_auth_dependency`` for multi-tenant mode.

Two modes:

- **Legacy (single-vault):** ``VAULT_TOKEN=<t>`` → one token, full access,
  actor ``vault-token``. Unset ⇒ auth disabled, everything open. The token
  registry (``VAULT_TOKENS`` env, ``registry/tokens.yaml``) maps tokens to
  ``{actor, scopes}``. Scope check is global (``read``/``write``/``resolve``).

- **Multi-tenant (MTAV):** tokens resolve to an ``Identity`` with per-vault
  grants. The request's target vault is resolved from the ``X-Vault`` header,
  ``/api/vaults/{vault}/...`` path prefix, or the §6.1 default-fallback rule.
  Scope checks are per-vault: the identity must have the required scope *on the
  target vault*. Grant-first, existence-second (SI-5). No client input reaches
  a path join (SI-2). The resolved vault path is stashed on
  ``request.state.vault_path`` for downstream endpoints.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import secrets
from typing import Annotated, Callable

from fastapi import HTTPException, Request, Security, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from agent_vault.api.config import Settings
from agent_vault.api.tenant import (
    ALL_SCOPES,
    ANONYMOUS,
    Identity,
    TenantRegistry,
    VaultResolver,
    get_registry,
)

# Legacy compat: keep these importable from auth.py
_RESOLVE_PATH_RE = re.compile(r"^/api/creds/[^/]+/resolve$")


def _slug_from_resolve_path(path: str) -> str | None:
    """Extract the entity slug from /api/creds/{slug}/resolve."""
    m = re.match(r"^/api/creds/([^/]+)/resolve$", path)
    return m.group(1) if m else None


def required_scope(method: str, path: str) -> str | None:
    """The scope a request needs, or None for open paths (health, non-/api).

    In multi-tenant mode the tenant control-plane endpoints (``/api/tenant/*``)
    are open at this layer — their own handlers enforce admin/elevated checks.
    """
    if not path.startswith("/api/") or path == "/api/health":
        return None
    # Tenant control plane — scoped internally
    if path.startswith("/api/tenant/"):
        return None
    if method == "POST" and _RESOLVE_PATH_RE.match(path):
        return "resolve"
    if method in ("POST", "PUT", "PATCH", "DELETE"):
        return "write"
    return "read"


# ---------------------------------------------------------------------------
# Legacy single-vault registry (unchanged from pre-MTAV)
# ---------------------------------------------------------------------------


def _identity_from_spec(token: str, spec: object) -> Identity:
    """Build a legacy Identity from a registry entry, defaulting conservatively."""
    if not isinstance(spec, dict):
        return Identity.legacy(token[:8] or "agent", frozenset({"read"}))
    actor = str(spec.get("actor") or token[:8] or "agent")
    raw_scopes = spec.get("scopes")
    if raw_scopes in (None, "", "all"):
        scopes = ALL_SCOPES
    elif isinstance(raw_scopes, (list, tuple, set)):
        scopes = frozenset(str(s) for s in raw_scopes)
    else:
        scopes = frozenset({str(raw_scopes)})
    return Identity.legacy(actor, scopes)


def load_token_registry(settings: Settings) -> dict[str, Identity]:
    """Merge all *legacy* token sources into {token: Identity}. Never raises.

    In multi-tenant mode, this returns an empty dict (tokens come from the
    TenantRegistry instead). This keeps ``_guard_open_bind`` working: it calls
    this to check whether auth is configured.
    """
    if settings.multi_tenant:
        registry = get_registry(settings)
        # In MTAV mode, auth is "configured" if there's at least one token
        # OR the legacy VAULT_TOKEN is set (bootstrap compat).
        registry._ensure_loaded()  # noqa: SLF001
        if registry._tokens or settings.token:  # noqa: SLF001
            return {"__mtav__": ANONYMOUS}  # non-empty = auth enabled
        return {}

    registry_data: dict[str, Identity] = {}

    # registry/tokens.yaml (human-authored, lowest precedence)
    path = os.path.join(settings.vault_path, "registry", "tokens.yaml")
    if os.path.exists(path):
        try:
            import yaml

            with open(path, encoding="utf-8") as fh:
                data = yaml.safe_load(fh) or {}
            for tok, spec in (data.get("tokens") or {}).items():
                registry_data[str(tok)] = _identity_from_spec(str(tok), spec)
        except Exception:  # noqa: BLE001 - a bad tokens file must not crash boot
            pass

    # VAULT_TOKENS env JSON
    raw = os.environ.get("VAULT_TOKENS", "").strip()
    if raw:
        try:
            data = json.loads(raw)
            if isinstance(data, dict):
                for tok, spec in data.items():
                    registry_data[str(tok)] = _identity_from_spec(str(tok), spec)
        except json.JSONDecodeError:
            pass

    # Legacy single token = admin (highest precedence)
    if settings.token:
        registry_data[settings.token] = Identity.legacy("vault-token", ALL_SCOPES)

    return registry_data


def _match_token_legacy(presented: str, registry: dict[str, Identity]) -> Identity | None:
    """Constant-time lookup: compare against every token so a match/miss can't
    be told apart by response timing."""
    found: Identity | None = None
    pres = presented.encode("utf-8")
    for tok, ident in registry.items():
        if secrets.compare_digest(pres, tok.encode("utf-8")):
            found = ident
    return found


# ---------------------------------------------------------------------------
# Multi-tenant auth dependency
# ---------------------------------------------------------------------------


def _resolve_token_mtav(
    presented: str,
    registry: TenantRegistry,
) -> Identity | None:
    """Resolve a presented token via the tenant registry (hashed lookup)."""
    return registry.match_token(presented)


def create_tenant_auth_dependency(settings: Settings) -> Callable[..., None]:
    """Build the FastAPI dependency for multi-tenant auth (§5.3 enforcement).

    This is the single enforcement point: it resolves token → identity →
    grants → target vault, scope-checks per-vault, and stashes the resolved
    vault path + name on ``request.state`` for downstream endpoints.

    SI-5: grant check happens BEFORE existence check, and both return the same
    error shape (403) so denied/nonexistent can't be distinguished.
    """
    registry = get_registry(settings)
    resolver = VaultResolver(registry, settings)

    def auth_dependency(
        request: Request,
        credentials: Annotated[
            HTTPAuthorizationCredentials | None,
            Security(HTTPBearer(auto_error=False)),
        ] = None,
    ) -> None:
        if credentials is None:
            request.state.identity = Identity.legacy("unknown", frozenset())
            request.state.vault_path = ""
            request.state.vault_name = ""
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Missing authorization header",
                headers={"WWW-Authenticate": "Bearer"},
            )

        ident = _resolve_token_mtav(credentials.credentials, registry)
        if ident is None:
            tok_tag = "token:" + hashlib.sha256(
                credentials.credentials.encode("utf-8")
            ).hexdigest()[:12]
            request.state.identity = Identity.legacy(tok_tag, frozenset())
            request.state.vault_path = ""
            request.state.vault_name = ""
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid authorization token",
                headers={"WWW-Authenticate": "Bearer"},
            )

        # --- resolve target vault (§6.1) ---
        # Skip resolution for tenant control-plane endpoints (whoami, vaults)
        # — these must work even for zero-grant tokens (Claude finding #3).
        path = request.url.path
        is_tenant_control_plane = path.startswith("/api/tenant/")
        if is_tenant_control_plane:
            vault_name = ""
            vault_path = ""
        else:
            x_vault = request.headers.get("x-vault")
            try:
                vault_name, vault_path = resolver.resolve(ident, x_vault, path)
            except ValueError:
                # SI-5: grant-first, existence-second, same error shape
                request.state.identity = ident
                request.state.vault_path = ""
                request.state.vault_name = ""
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="access denied",
                ) from None

        # Stash resolved vault on request state for downstream endpoints
        ident_resolved = Identity(
            actor=ident.actor,
            grants=ident.grants,
            system=ident.system,
            vault_path=vault_path,
            vault_name=vault_name,
        )
        request.state.identity = ident_resolved
        request.state.vault_path = vault_path
        request.state.vault_name = vault_name

        # --- scope check (per-vault) ---
        scope = required_scope(request.method, request.url.path)
        if scope == "resolve":
            # Per-secret grants: use can_resolve with the slug from the path
            # so a per-secret grant works (Codex finding #1 fix).
            slug = _slug_from_resolve_path(request.url.path)
            if not ident_resolved.can_resolve(vault_name, slug):
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="access denied",
                )
        elif scope:
            if not ident_resolved.has_scope(vault_name, scope):
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail=f"token '{ident.actor}' lacks scope '{scope}' on vault '{vault_name}'",
                )
        return None

    return auth_dependency


# ---------------------------------------------------------------------------
# Unified factory — picks the right dependency based on settings
# ---------------------------------------------------------------------------


def create_auth_dependency(settings: Settings) -> Callable[..., None]:
    """Build the FastAPI auth dependency appropriate for the configured mode.

    - ``multi_tenant=True`` → tenant-aware dependency (per-vault grants).
    - ``multi_tenant=False`` → legacy single-vault dependency (unchanged).
    """
    if settings.multi_tenant:
        return create_tenant_auth_dependency(settings)

    # --- legacy single-vault path (unchanged from pre-MTAV) ---
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
            request.state.vault_path = settings.vault_path
            request.state.vault_name = ""
            return None

        if credentials is None:
            request.state.identity = Identity.legacy("unknown", frozenset())
            request.state.vault_path = settings.vault_path
            request.state.vault_name = ""
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Missing authorization header",
                headers={"WWW-Authenticate": "Bearer"},
            )
        ident = _match_token_legacy(credentials.credentials, registry)
        if ident is None:
            tok_tag = "token:" + hashlib.sha256(
                credentials.credentials.encode("utf-8")
            ).hexdigest()[:12]
            request.state.identity = Identity.legacy(tok_tag, frozenset())
            request.state.vault_path = settings.vault_path
            request.state.vault_name = ""
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid authorization token",
                headers={"WWW-Authenticate": "Bearer"},
            )
        request.state.identity = ident
        request.state.vault_path = settings.vault_path
        request.state.vault_name = ""
        scope = required_scope(request.method, request.url.path)
        if scope and not ident.allows(scope):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"token '{ident.actor}' lacks required scope '{scope}'",
            )
        return None

    return auth_dependency

"""Append-only access audit for the Agent Vault API (O5).

A pure-ASGI middleware records who did what, centrally, so a shared multi-agent
deployment has an attribution trail. After the route (including its auth
dependency) has run, it appends one line to ``discovery/_access.jsonl`` for each
MUTATING or credential-RESOLVING call:

    {ts, actor, method, path, status, scope, action, target, vault}

It NEVER records request/response bodies, so a resolved secret is never written
(the resolve line carries only the entity slug + outcome status). Reads are not
audited. Pure ASGI (not BaseHTTPMiddleware) so it does not buffer streaming
responses (the SSE job stream keeps working). Best-effort: an audit failure
never affects the response.

**Phase 1 (MTAV):** the audit middleware now reads the *resolved vault path*
from ASGI scope state (``scope["state"]["vault_path"]``) rather than the static
``vault_path`` captured at app-construction time. This is the R3 finding fix:
the audit trail must reflect which vault a request actually touched, not the
global default. In legacy single-vault mode, ``vault_path`` is still set on
``request.state`` by the auth dependency, so the middleware reads it the same
way.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from typing import Any

from starlette.types import ASGIApp, Message, Receive, Scope, Send

from agent_vault.api.auth import required_scope


def _slug_from_path(path: str) -> str:
    """Best-effort target extraction (e.g. /api/creds/<slug>/resolve -> slug)."""
    parts = [p for p in path.split("/") if p]
    if len(parts) >= 3 and parts[0] == "api":
        return parts[2]
    return ""


def write_audit(vault: str, record: dict[str, Any]) -> None:
    """Append one audit record to discovery/_access.jsonl. Best-effort."""
    try:
        disc = os.path.join(vault, "discovery")
        os.makedirs(disc, exist_ok=True)
        with open(os.path.join(disc, "_access.jsonl"), "a", encoding="utf-8") as fh:
            fh.write(json.dumps(record) + "\n")
    except OSError:
        pass


class AuditMiddleware:
    """Audit mutating + resolve requests with the caller's resolved identity.

    The ``vault_path`` constructor arg is kept for backward compat (tests that
    construct the middleware directly still work), but in MTAV mode the
    middleware prefers the per-request resolved vault from ASGI scope state.
    """

    def __init__(self, app: ASGIApp, vault_path: str = "") -> None:
        self.app = app
        self.vault_path = vault_path

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        needed = required_scope(scope.get("method", ""), scope.get("path", ""))
        if needed not in ("write", "resolve"):
            await self.app(scope, receive, send)
            return

        status_code = {"v": 0}

        async def send_wrapper(message: Message) -> None:
            if message["type"] == "http.response.start":
                status_code["v"] = int(message["status"])
            await send(message)

        await self.app(scope, receive, send_wrapper)

        # Read the resolved identity + vault from ASGI scope state (set by the
        # auth dependency). In legacy mode, vault_path comes from request.state
        # (set by the legacy auth dependency); in MTAV mode, it's the per-
        # request resolved vault. Either way, scope["state"] is the source.
        state = scope.get("state") or {}
        identity = state.get("identity")
        actor = getattr(identity, "actor", "anonymous")
        # Prefer the per-request resolved vault; fall back to the static
        # constructor arg for pre-MTAV tests that don't set request.state.
        vault = state.get("vault_path") or self.vault_path
        path = scope.get("path", "")
        record = {
            "ts": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "actor": actor,
            "method": scope.get("method", ""),
            "path": path,
            "status": status_code["v"],
            "scope": needed,
            "action": "resolve" if needed == "resolve" else str(scope.get("method", "")).lower(),
            "target": _slug_from_path(path),
            "vault": state.get("vault_name", ""),
        }
        write_audit(vault, record)

"""Append-only access audit for the Agent Vault API (O5).

A pure-ASGI middleware records who did what, centrally, so a shared multi-agent
deployment has an attribution trail. After the route (including its auth
dependency) has run, it appends one line to `discovery/_access.jsonl` for each
MUTATING or credential-RESOLVING call:

    {ts, actor, method, path, status, scope, action, target}

It NEVER records request/response bodies, so a resolved secret is never written
(the resolve line carries only the entity slug + outcome status). Reads are not
audited. Pure ASGI (not BaseHTTPMiddleware) so it does not buffer streaming
responses (the SSE job stream keeps working). Best-effort: an audit failure
never affects the response.
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
    """Audit mutating + resolve requests with the caller's resolved identity."""

    def __init__(self, app: ASGIApp, vault_path: str) -> None:
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

        # The auth dependency stored the Identity on request.state, which is
        # backed by scope["state"]; read it back here for attribution.
        identity = (scope.get("state") or {}).get("identity")
        actor = getattr(identity, "actor", "anonymous")
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
        }
        write_audit(self.vault_path, record)

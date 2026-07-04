#!/usr/bin/env python3
"""mcp_server.py — Model Context Protocol server for Agent Vault.

Exposes the shared knowledgebase to any MCP-capable agent as first-class tools,
so a fleet of agents can read and contribute without hand-rolling HTTP:

    vault_search        ranked full-text search over prose + metadata
    vault_get           full record for one entity (facts, prose, links)
    vault_list          entity summaries, optionally filtered by type
    vault_status        counts + last pipeline run
    vault_submit_source contribute a NEW document into raw/ (append-only)
    vault_resolve_credential   resolve a credential_ref (opt-in; off by default)

The tool LOGIC lives in `mcp_tools.py` (plain, testable, no MCP dependency).
This module is only the thin FastMCP wiring, so the `mcp` package is an OPTIONAL
extra: `pip install "agent-vault[mcp]"`. The server runs IN-PROCESS against the
vault files (no network), so run it where the vault lives. It selects the vault
from AGENT_VAULT_PATH (default ".").

Run:  agent-vault-mcp        # stdio transport (how MCP clients launch it)
"""
from __future__ import annotations

import os
import sys
from typing import Any

from agent_vault import mcp_tools


def build_mcp(vault: str | None = None) -> Any:
    """Build a FastMCP server bound to a vault directory.

    Imports `mcp` lazily so this module (and the tool functions it wraps) stay
    importable without the optional extra installed. Raises a clear error if the
    extra is missing.
    """
    try:
        from mcp.server.fastmcp import FastMCP
    except ImportError as e:  # pragma: no cover - exercised only without the extra
        raise RuntimeError(
            "the MCP server needs the optional 'mcp' extra: "
            "pip install \"agent-vault[mcp]\""
        ) from e

    vault = vault or os.environ.get("AGENT_VAULT_PATH", ".")
    server = FastMCP("agent-vault")

    @server.tool()
    def vault_search(query: str, limit: int = 20, mode: str = "fts") -> dict[str, Any]:
        """Ranked retrieval over entity prose AND metadata — the primary way to
        recall knowledge from the shared wiki. mode: 'fts' (keyword/bm25, no
        model), 'semantic' (embedding cosine), or 'hybrid' (fusion of both)."""
        return mcp_tools.search(vault, query, limit, mode)

    @server.tool()
    def vault_ask(question: str, k: int = 5, mode: str = "hybrid") -> dict[str, Any]:
        """Grounded, cited answer to a natural-language question. Retrieves the
        top-k entities and constrains a local model to their prose; returns the
        answer plus the entities it cited. Never invents facts."""
        return mcp_tools.ask(vault, question, k, mode)

    @server.tool()
    def vault_get(slug: str) -> dict[str, Any]:
        """Full record for one entity: facts, prose, sources, and resolved
        links. Credentials appear as references only (use
        vault_resolve_credential for the secret)."""
        return mcp_tools.get_entity(vault, slug)

    @server.tool()
    def vault_list(type: str = "", limit: int = 100) -> dict[str, Any]:
        """List entity summaries, optionally filtered by type (e.g. 'account',
        'asset', 'document')."""
        return mcp_tools.list_entities(vault, type, limit)

    @server.tool()
    def vault_status() -> dict[str, Any]:
        """Vault snapshot: entity counts by status/type and the last pipeline
        run."""
        return mcp_tools.status(vault)

    @server.tool()
    def vault_submit_source(filename: str, content: str,
                            subdir: str = "documents", actor: str = "") -> dict[str, Any]:
        """Contribute a NEW source document to the vault. Writes it into raw/ for
        the next ingest pass to classify deterministically. Append-only: refuses
        to overwrite. Pass `actor` to attribute the contribution."""
        return mcp_tools.submit_source(vault, filename, content, subdir, actor)

    @server.tool()
    def vault_resolve_credential(slug: str) -> dict[str, Any]:
        """Resolve an entity's credential_ref to its secret. Disabled unless
        AGENT_VAULT_MCP_ALLOW_RESOLVE=1 is set on the server."""
        return mcp_tools.resolve_credential(vault, slug)

    return server


def main() -> int:
    """Console entry point (`agent-vault-mcp`): run the stdio MCP server."""
    try:
        server = build_mcp()
    except RuntimeError as e:
        print(str(e), file=sys.stderr)
        return 1
    server.run()  # stdio transport by default
    return 0


if __name__ == "__main__":
    sys.exit(main())

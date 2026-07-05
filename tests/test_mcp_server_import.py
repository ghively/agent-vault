"""Smoke test for the optional MCP server wiring.

The `mcp` SDK is an optional extra (`pip install agent-vault[mcp]`), so this is
skipped when it isn't installed. In CI it runs in the dedicated mcp-extra job,
which installs the extra — keeping the FastMCP wiring from silently rotting
(e.g. a tool renamed in mcp_tools.py but not re-registered here).
"""

import pytest

pytest.importorskip("mcp", reason="optional [mcp] extra not installed")


def test_mcp_server_imports_and_registers_tools():
    import agent_vault.mcp_server as mcp_server

    # The module builds a FastMCP server at import time; a broken decorator or a
    # renamed tool fn would raise on import.
    assert hasattr(mcp_server, "main")

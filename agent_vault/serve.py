"""Agent Vault API service entry point.

Run with: agent-vault-serve
Or: python -m agent_vault.serve
"""

from __future__ import annotations

import uvicorn

from agent_vault.api.app import create_app
from agent_vault.api.config import Settings


def main() -> None:
    """Main entry point for the API service.

    Reads configuration from environment and starts the uvicorn server.
    """
    settings = Settings.from_env()
    settings.validate()  # fail fast on a bad vault path (B1)
    app = create_app(settings)

    uvicorn.run(
        app,
        host=settings.host,
        port=settings.port,
        log_level="info",
    )


if __name__ == "__main__":
    main()

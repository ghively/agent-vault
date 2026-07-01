"""Configuration for Agent Vault API service.

Settings are read from environment variables with sensible defaults.
No external dependencies — plain dataclass reading os.environ directly.
"""

from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Settings:
    """API service configuration.

    Attributes:
        host: Bind address (default: 127.0.0.1)
        port: TCP port (default: 7778)
        vault_path: Path to vault data directory (default: ".")
        token: Optional bearer token for auth (default: "" = no auth)
    """

    host: str = "127.0.0.1"
    port: int = 7778
    vault_path: str = "."
    token: str = ""

    @classmethod
    def from_env(cls) -> Settings:
        """Create Settings from environment variables.

        Env vars:
            AGENT_VAULT_HOST: bind address (default: 127.0.0.1)
            AGENT_VAULT_PORT: port number (default: 7778)
            AGENT_VAULT_PATH: vault data directory (default: ".")
            VAULT_TOKEN: bearer token (default: "" = no auth)

        Returns:
            Settings instance with values from env or defaults
        """
        return cls(
            host=os.environ.get("AGENT_VAULT_HOST", "127.0.0.1"),
            port=int(os.environ.get("AGENT_VAULT_PORT", "7778")),
            vault_path=os.environ.get("AGENT_VAULT_PATH", "."),
            token=os.environ.get("VAULT_TOKEN", ""),
        )

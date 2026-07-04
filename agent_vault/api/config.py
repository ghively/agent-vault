"""Configuration for Agent Vault API service.

Settings are read from environment variables with sensible defaults.
No external dependencies — plain dataclass reading os.environ directly.
"""

from __future__ import annotations

import os
import sys
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
        raw_port = os.environ.get("AGENT_VAULT_PORT", "7778")
        try:
            port = int(raw_port)
        except ValueError as e:
            raise SystemExit(
                f"AGENT_VAULT_PORT must be an integer, got {raw_port!r}: {e}"
            ) from e
        if not (0 < port < 65536):
            raise SystemExit(f"AGENT_VAULT_PORT out of range (1-65535): {port}")
        return cls(
            host=os.environ.get("AGENT_VAULT_HOST", "127.0.0.1"),
            port=port,
            vault_path=os.environ.get("AGENT_VAULT_PATH", "."),
            token=os.environ.get("VAULT_TOKEN", ""),
        )

    def validate(self) -> None:
        """Fail fast with an actionable message if the vault path is wrong.

        Called at service startup (serve.py) so a misconfigured AGENT_VAULT_PATH
        surfaces as a clear boot error instead of confusing empty reads / 404s.
        """
        if not os.path.isdir(self.vault_path):
            print(f"error: AGENT_VAULT_PATH {self.vault_path!r} is not a directory "
                  f"(set it to your vault root, containing entities/ and registry/)",
                  file=sys.stderr)
            raise SystemExit(2)

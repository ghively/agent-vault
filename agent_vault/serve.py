"""Agent Vault API service entry point.

Run with: agent-vault-serve
Or: python -m agent_vault.serve
"""

from __future__ import annotations

import os
import sys

import uvicorn

from agent_vault.api.app import create_app
from agent_vault.api.auth import load_token_registry
from agent_vault.api.config import Settings

# Hosts that only accept connections from the local machine. Binding to one of
# these with auth disabled is fine; binding to a routable address is not.
_LOOPBACK_HOSTS = {"127.0.0.1", "::1", "localhost", ""}


def _guard_open_bind(settings: Settings) -> None:
    """Fail closed: refuse to expose an unauthenticated vault on the network.

    When no token registry is configured, every /api call is open (actor
    ``anonymous``, all scopes). That is safe on loopback (the default) but a
    silent data-exposure risk on a routable bind. Require an explicit
    ``AGENT_VAULT_ALLOW_OPEN=1`` opt-in to run open on a non-loopback host, so
    the exposure is always a deliberate choice rather than a defaulted one.
    """
    if settings.host in _LOOPBACK_HOSTS:
        return
    if load_token_registry(settings):
        return  # auth is configured — fine to bind anywhere
    if os.environ.get("AGENT_VAULT_ALLOW_OPEN") == "1":
        print(
            f"warning: binding {settings.host}:{settings.port} with NO auth "
            f"(AGENT_VAULT_ALLOW_OPEN=1) — the vault is readable/writable by "
            f"anyone who can reach this address.",
            file=sys.stderr,
        )
        return
    print(
        f"error: refusing to bind {settings.host}:{settings.port} with auth "
        f"disabled — an unauthenticated vault would be exposed on the network.\n"
        f"  Configure a token (VAULT_TOKEN / registry/tokens.yaml / VAULT_TOKENS),\n"
        f"  or set AGENT_VAULT_ALLOW_OPEN=1 to deliberately run open, or bind to\n"
        f"  127.0.0.1 (the default) and put a TLS-terminating proxy in front.",
        file=sys.stderr,
    )
    raise SystemExit(2)


def main() -> None:
    """Main entry point for the API service.

    Reads configuration from environment and starts the uvicorn server.
    """
    settings = Settings.from_env()
    settings.validate()  # fail fast on a bad vault path (B1)
    _guard_open_bind(settings)  # fail closed on an open, network-exposed bind
    app = create_app(settings)

    uvicorn.run(
        app,
        host=settings.host,
        port=settings.port,
        log_level="info",
    )


if __name__ == "__main__":
    main()

"""Configuration for Agent Vault API service.

Settings are read from environment variables with sensible defaults.
No external dependencies — plain dataclass reading os.environ directly.

Phase 1 (MTAV) adds multi-tenant fields (multi_tenant, vaults_root,
enroll_secret, require_secret_approval). When ``multi_tenant=False`` (the
default), behaviour is identical to the pre-MTAV single-vault model.
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
        multi_tenant: Enable multi-tenant mode (default: False)
        vaults_root: Root dir for all enclave trees (MTAV mode)
        enroll_secret: Pre-shared onboarding secret (MTAV Phase 2)
        require_secret_approval: Force propose→promote on secret creation
    """

    host: str = "127.0.0.1"
    port: int = 7778
    vault_path: str = "."
    token: str = ""
    # MTAV Phase 1+ fields
    multi_tenant: bool = False
    vaults_root: str = ""
    # MTAV Phase 2 fields (read now, used later)
    enroll_secret: str = ""
    require_secret_approval: bool = False

    @classmethod
    def from_env(cls) -> Settings:
        """Create Settings from environment variables.

        Env vars:
            AGENT_VAULT_HOST: bind address (default: 127.0.0.1)
            AGENT_VAULT_PORT: port number (default: 7778)
            AGENT_VAULT_PATH: vault data directory (default: ".")
            VAULT_TOKEN: bearer token (default: "" = no auth)
            AGENT_VAULT_MULTI_TENANT: enable MTAV (default: "0")
            MTAV_VAULTS_ROOT: root dir for enclave trees (default: "~/.agent-vaults")
            MTAV_ENROLL_SECRET: onboarding secret (default: "")
            MTAV_REQUIRE_SECRET_APPROVAL: force secret review (default: "0")

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

        multi_tenant = os.environ.get("AGENT_VAULT_MULTI_TENANT", "0") in ("1", "true", "yes")
        vaults_root = os.environ.get(
            "MTAV_VAULTS_ROOT",
            os.path.expanduser("~/.agent-vaults") if multi_tenant else "",
        )

        return cls(
            host=os.environ.get("AGENT_VAULT_HOST", "127.0.0.1"),
            port=port,
            vault_path=os.environ.get("AGENT_VAULT_PATH", "."),
            token=os.environ.get("VAULT_TOKEN", ""),
            multi_tenant=multi_tenant,
            vaults_root=vaults_root,
            enroll_secret=os.environ.get("MTAV_ENROLL_SECRET", ""),
            require_secret_approval=os.environ.get(
                "MTAV_REQUIRE_SECRET_APPROVAL", "0"
            ) in ("1", "true", "yes"),
        )

    def validate(self) -> None:
        """Fail fast with an actionable message if the vault path is wrong.

        Called at service startup (serve.py) so a misconfigured AGENT_VAULT_PATH
        surfaces as a clear boot error instead of confusing empty reads / 404s.

        In multi-tenant mode, validates vaults_root instead: it must be a dir,
        contain ``_tenant/vaults.yaml``, and have at least one vault registered.
        """
        if self.multi_tenant:
            root = os.path.expanduser(self.vaults_root)
            if not os.path.isdir(root):
                print(
                    f"error: MTAV_VAULTS_ROOT {root!r} is not a directory "
                    f"(set it to the root containing _tenant/ and your vault trees)",
                    file=sys.stderr,
                )
                raise SystemExit(2)
            vfile = os.path.join(root, "_tenant", "vaults.yaml")
            if not os.path.exists(vfile):
                print(
                    f"error: {vfile!r} not found — run the migration script "
                    f"(agent-vault-migrate-to-mtav) to initialise the tenant registry",
                    file=sys.stderr,
                )
                raise SystemExit(2)
            # Check at least one vault is registered
            try:
                import yaml
                with open(vfile, encoding="utf-8") as fh:
                    data = yaml.safe_load(fh) or {}
                vaults = data.get("vaults") or {}
                if not vaults:
                    print(
                        f"error: no vaults registered in {vfile!r}",
                        file=sys.stderr,
                    )
                    raise SystemExit(2)
            except (OSError, Exception) as e:  # noqa: BLE001
                print(f"error: cannot parse {vfile!r}: {e}", file=sys.stderr)
                raise SystemExit(2)
        else:
            if not os.path.isdir(self.vault_path):
                print(
                    f"error: AGENT_VAULT_PATH {self.vault_path!r} is not a directory "
                    f"(set it to your vault root, containing entities/ and registry/)",
                    file=sys.stderr,
                )
                raise SystemExit(2)

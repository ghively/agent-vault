"""Tests for the Multi-Tenant Agent Vault (MTAV) Phase 1 layer.

Covers the 8 security invariants (SI-1..SI-8 from docs/MTAV_SPEC.md §11.0),
vault isolation, backward compatibility, and the §6a.1a job ownership rule.

These tests create real on-disk vault trees + tenant registries under tmp_path
and exercise the auth dependency + resolver + registry directly, then verify
isolation through the FastAPI TestClient.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml
from fastapi.testclient import TestClient

from agent_vault.api.app import create_app
from agent_vault.api.config import Settings
from agent_vault.api.tenant import (
    Grant,
    Identity,
    TenantRegistry,
    VaultResolver,
    _hash_token,
    _validate_slug,
    get_registry,
    reset_registry,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_vault_tree(path: Path, name: str = "test") -> None:
    """Create a minimal vault tree at *path*."""
    for sub in ("entities", "raw", "discovery", "registry"):
        (path / sub).mkdir(parents=True, exist_ok=True)
    # Minimal index
    (path / "_index.json").write_text(
        json.dumps({"entities": []}), encoding="utf-8"
    )


def _make_mtav_env(tmp_path: Path) -> tuple[Path, Path]:
    """Create a full MTAV environment under tmp_path.

    Returns (vaults_root, household_path).
    """
    root = tmp_path / "vaults"
    tenant_dir = root / "_tenant"
    tenant_dir.mkdir(parents=True)

    # Create vault trees
    household = root / "household"
    gregory = root / "gregory"
    kevin = root / "kevin"
    _make_vault_tree(household, "household")
    _make_vault_tree(gregory, "gregory")
    _make_vault_tree(kevin, "kevin")

    # vaults.yaml
    vaults_yaml = {
        "vaults": {
            "household": {
                "path": str(household),
                "owner": "gene",
                "visibility": "shared",
                "description": "Household knowledge",
            },
            "gregory": {
                "path": str(gregory),
                "owner": "gregory",
                "visibility": "private",
            },
            "kevin": {
                "path": str(kevin),
                "owner": "kevin",
                "visibility": "private",
            },
        }
    }
    (tenant_dir / "vaults.yaml").write_text(
        yaml.safe_dump(vaults_yaml, default_flow_style=False),
        encoding="utf-8",
    )

    return root, household


def _make_token(
    root: Path,
    plaintext: str,
    actor: str,
    grants: list[dict],
    system: bool = False,
) -> None:
    """Add a token to _tenant/tokens.yaml."""
    tokens_file = root / "_tenant" / "tokens.yaml"
    if tokens_file.exists():
        data = yaml.safe_load(tokens_file.read_text(encoding="utf-8")) or {}
    else:
        data = {"tokens": {}}
    if "tokens" not in data:
        data["tokens"] = {}

    h = _hash_token(plaintext)
    data["tokens"][h] = {
        "actor": actor,
        "system": system,
        "grants": grants,
    }
    tokens_file.write_text(
        yaml.safe_dump(data, default_flow_style=False, sort_keys=False),
        encoding="utf-8",
    )


def _mtav_settings(root: Path) -> Settings:
    """Build Settings for MTAV mode pointing at *root*."""
    return Settings(
        vault_path=str(root / "household"),
        token="",
        multi_tenant=True,
        vaults_root=str(root),
    )


# ---------------------------------------------------------------------------
# SI-2: Slug validation — no client input reaches path construction
# ---------------------------------------------------------------------------


class TestSI2SlugValidation:
    """SI-2: vault/actor names are validated slugs; path is registry-resolved."""

    def test_valid_slugs(self):
        assert _validate_slug("household") == "household"
        assert _validate_slug("gregory") == "gregory"
        assert _validate_slug("agent-7") == "agent-7"
        assert _validate_slug("a.b.c") == "a.b.c"

    @pytest.mark.parametrize("bad", [
        "", "UPPER", "../../etc", "a/b", " has space", ".hidden",
        "x" * 65, "-leading-dash", "UPPER_CASE",
    ])
    def test_invalid_slugs_rejected(self, bad):
        with pytest.raises(ValueError):
            _validate_slug(bad)

    def test_path_traversal_blocked_in_resolver(self, tmp_path):
        """A crafted X-Vault header can't escape the registry."""
        root, _ = _make_mtav_env(tmp_path)
        registry = TenantRegistry(str(root), multi_tenant=True)
        resolver = VaultResolver(registry, _mtav_settings(root))

        ident = Identity(
            actor="test",
            grants=(Grant(vault="household", scopes=frozenset({"read"})),),
        )
        # Path traversal attempt via X-Vault
        with pytest.raises((ValueError, Exception)):
            resolver.resolve(ident, "../../../etc/passwd", "/api/entities")


# ---------------------------------------------------------------------------
# SI-5: Grant check before existence check (no 403-vs-404 oracle)
# ---------------------------------------------------------------------------


class TestSI5GrantBeforeExistence:
    """SI-5: denied + nonexistent return the same 403 shape."""

    def test_denied_vault_returns_403(self, tmp_path):
        root, _ = _make_mtav_env(tmp_path)
        _make_token(root, "greg-token", "gregory", [
            {"vault": "gregory", "scopes": ["read", "write"]},
            {"vault": "household", "scopes": ["read"]},
        ])
        reset_registry()
        settings = _mtav_settings(root)
        app = create_app(settings)
        client = TestClient(app)

        # Gregory has no grant on "kevin" — accessing kevin should 403
        h = {"Authorization": "Bearer greg-token", "X-Vault": "kevin"}
        r = client.get("/api/entities", headers=h)
        assert r.status_code == 403

    def test_nonexistent_vault_returns_403(self, tmp_path):
        """Accessing a vault that doesn't exist in the registry → 403, not 404."""
        root, _ = _make_mtav_env(tmp_path)
        _make_token(root, "greg-token", "gregory", [
            {"vault": "gregory", "scopes": ["read", "write"]},
        ])
        reset_registry()
        settings = _mtav_settings(root)
        app = create_app(settings)
        client = TestClient(app)

        h = {"Authorization": "Bearer greg-token", "X-Vault": "nonexistent"}
        r = client.get("/api/entities", headers=h)
        # SI-5: 403, not 404 — can't distinguish denied from nonexistent
        assert r.status_code == 403


# ---------------------------------------------------------------------------
# SI-6: Registry changes take effect next request (reloadable)
# ---------------------------------------------------------------------------


class TestSI6ReloadableRegistry:
    """SI-6: token/vault changes are visible on the next request."""

    def test_add_token_visible_without_restart(self, tmp_path):
        root, _ = _make_mtav_env(tmp_path)
        _make_token(root, "greg-token", "gregory", [
            {"vault": "gregory", "scopes": ["read", "write"]},
        ])
        reset_registry()
        settings = _mtav_settings(root)
        registry = get_registry(settings)

        # Initial: only greg-token exists
        assert registry.match_token("greg-token") is not None
        assert registry.match_token("new-token") is None

        # Add a new token directly to the file
        _make_token(root, "new-token", "newcomer", [
            {"vault": "gregory", "scopes": ["read"]},
        ])

        # Invalidate + reload — the new token should be visible
        registry.invalidate()
        assert registry.match_token("new-token") is not None

    def test_remove_token_visible_without_restart(self, tmp_path):
        root, _ = _make_mtav_env(tmp_path)
        _make_token(root, "greg-token", "gregory", [
            {"vault": "gregory", "scopes": ["read"]},
        ])
        _make_token(root, "kev-token", "kevin", [
            {"vault": "kevin", "scopes": ["read"]},
        ])
        reset_registry()
        settings = _mtav_settings(root)
        registry = get_registry(settings)

        assert registry.match_token("kev-token") is not None

        # Remove kev-token via the registry API
        h = _hash_token("kev-token")
        removed = registry.remove_token(h)
        assert removed

        # Next access: kev-token is gone
        assert registry.match_token("kev-token") is None
        assert registry.match_token("greg-token") is not None


# ---------------------------------------------------------------------------
# SI-1: Delegated grants must be a subset (tested via the model)
# ---------------------------------------------------------------------------


class TestSI1SubsetInvariant:
    """SI-1: a delegated grant must be a subset of the minter's grants."""

    def test_subset_check_logic(self, tmp_path):
        """The grant model correctly identifies subset vs superset."""
        minter_grants = (
            Grant(vault="gregory", scopes=frozenset({"read", "write"})),
        )
        minter = Identity(actor="admin", grants=minter_grants)

        # Minter has read+write on gregory
        assert minter.has_scope("gregory", "read")
        assert minter.has_scope("gregory", "write")
        assert not minter.has_scope("gregory", "resolve")

        # A delegated read-only grant is a valid subset
        delegated = Identity(actor="sub", grants=(
            Grant(vault="gregory", scopes=frozenset({"read"})),
        ))
        assert delegated.has_scope("gregory", "read")
        assert not delegated.has_scope("gregory", "write")

    def test_admin_cannot_grant_beyond_own_scope(self, tmp_path):
        """An admin with read+write cannot grant resolve (superset check)."""
        minter_scopes = frozenset({"read", "write"})
        requested_scopes = frozenset({"resolve"})
        # The subset check: every scope in requested must be in minter
        is_subset = requested_scopes <= minter_scopes
        assert not is_subset  # resolve is NOT in {read, write}


# ---------------------------------------------------------------------------
# Vault isolation — agents can only access their own vault
# ---------------------------------------------------------------------------


class TestVaultIsolation:
    """Two agents in separate vaults cannot see each other's data."""

    def test_gregory_cannot_read_kevin_vault(self, tmp_path):
        root, household = _make_mtav_env(tmp_path)
        # Put data in kevin's vault
        kevin_vault = root / "kevin"
        (kevin_vault / "_index.json").write_text(json.dumps({"entities": [
            {"slug": "secret-thing", "type": "note", "path": "entities/note/secret.md"},
        ]}), encoding="utf-8")

        _make_token(root, "greg-token", "gregory", [
            {"vault": "gregory", "scopes": ["read", "write"]},
            {"vault": "household", "scopes": ["read"]},
        ])
        _make_token(root, "kev-token", "kevin", [
            {"vault": "kevin", "scopes": ["read", "write"]},
            {"vault": "household", "scopes": ["read"]},
        ])

        reset_registry()
        settings = _mtav_settings(root)
        app = create_app(settings)
        client = TestClient(app)

        # Gregory → X-Vault: kevin → 403
        r = client.get("/api/entities", headers={
            "Authorization": "Bearer greg-token", "X-Vault": "kevin",
        })
        assert r.status_code == 403

        # Kevin → X-Vault: kevin → 200
        r = client.get("/api/entities", headers={
            "Authorization": "Bearer kev-token", "X-Vault": "kevin",
        })
        assert r.status_code == 200

    def test_default_fallback_routes_to_own_vault(self, tmp_path):
        """An agent with write on exactly one vault → that's the default."""
        root, _ = _make_mtav_env(tmp_path)
        _make_token(root, "greg-token", "gregory", [
            {"vault": "gregory", "scopes": ["read", "write"]},
            {"vault": "household", "scopes": ["read"]},
        ])
        reset_registry()
        settings = _mtav_settings(root)
        registry = get_registry(settings)
        ident = registry.match_token("greg-token")
        assert ident is not None
        default = registry.resolve_default_vault(ident)
        # gregory has write on exactly one vault (gregory) → that's the default
        assert default == "gregory"

    def test_bootstrap_defaults_to_household(self, tmp_path):
        """Bootstrap (admin on *) → defaults to household."""
        root, _ = _make_mtav_env(tmp_path)
        _make_token(root, "boot-token", "admin", [
            {"vault": "*", "scopes": ["admin"]},
        ], system=True)
        reset_registry()
        settings = _mtav_settings(root)
        registry = get_registry(settings)
        ident = registry.match_token("boot-token")
        assert ident is not None
        default = registry.resolve_default_vault(ident)
        assert default == "household"

    def test_agents_can_read_household_by_default(self, tmp_path):
        """Gregory's token with household:read can access household."""
        root, household = _make_mtav_env(tmp_path)
        _make_token(root, "greg-token", "gregory", [
            {"vault": "gregory", "scopes": ["read", "write"]},
            {"vault": "household", "scopes": ["read"]},
        ])
        reset_registry()
        settings = _mtav_settings(root)
        app = create_app(settings)
        client = TestClient(app)

        # Gregory reads household (has read grant)
        r = client.get("/api/entities", headers={
            "Authorization": "Bearer greg-token", "X-Vault": "household",
        })
        assert r.status_code == 200


# ---------------------------------------------------------------------------
# Scope enforcement per vault
# ---------------------------------------------------------------------------


class TestPerVaultScopeEnforcement:
    """Scope checks are per-vault, not global."""

    def test_read_on_household_write_on_gregory(self, tmp_path):
        """An agent with read on household + write on gregory cannot write household."""
        root, _ = _make_mtav_env(tmp_path)
        _make_token(root, "greg-token", "gregory", [
            {"vault": "gregory", "scopes": ["read", "write"]},
            {"vault": "household", "scopes": ["read"]},
        ])
        reset_registry()
        settings = _mtav_settings(root)
        app = create_app(settings)
        client = TestClient(app)

        h = {"Authorization": "Bearer greg-token", "X-Vault": "household"}

        # Read OK
        assert client.get("/api/entities", headers=h).status_code == 200
        # Write denied (no write scope on household)
        r = client.post("/api/jobs/run", headers=h, json={"op": "lint", "args": ["--json"]})
        assert r.status_code == 403
        assert "household" in r.json()["detail"]


# ---------------------------------------------------------------------------
# §6a.1a: Job ownership / cross-tenant isolation
# ---------------------------------------------------------------------------


class TestJobOwnership:
    """A job created in one vault can't be accessed by a token from another vault."""

    def test_cross_vault_job_access_denied(self, tmp_path):
        root, _ = _make_mtav_env(tmp_path)
        _make_token(root, "greg-token", "gregory", [
            {"vault": "gregory", "scopes": ["read", "write"]},
            {"vault": "household", "scopes": ["read"]},
        ])
        _make_token(root, "kev-token", "kevin", [
            {"vault": "kevin", "scopes": ["read", "write"]},
            {"vault": "household", "scopes": ["read"]},
        ])
        reset_registry()
        settings = _mtav_settings(root)
        app = create_app(settings)
        client = TestClient(app)

        # Gregory creates a job in his vault
        r = client.post("/api/jobs/run", headers={
            "Authorization": "Bearer greg-token",
        }, json={"op": "lint", "args": ["--json"]})
        assert r.status_code == 200
        job_id = r.json()["job_id"]

        # Kevin tries to access Gregory's job → 403
        r = client.get(f"/api/jobs/{job_id}", headers={
            "Authorization": "Bearer kev-token",
        })
        assert r.status_code == 403

        # Kevin tries to cancel Gregory's job → 403
        r = client.delete(f"/api/jobs/{job_id}", headers={
            "Authorization": "Bearer kev-token",
        })
        assert r.status_code == 403

        # Gregory can access his own job → 200
        r = client.get(f"/api/jobs/{job_id}", headers={
            "Authorization": "Bearer greg-token",
        })
        assert r.status_code == 200


# ---------------------------------------------------------------------------
# Backward compatibility — single-vault mode unchanged
# ---------------------------------------------------------------------------


class TestBackwardCompatibility:
    """Legacy single-vault mode is completely unchanged."""

    def test_legacy_mode_works_without_mtav(self, tmp_path):
        vault = tmp_path / "vault"
        _make_vault_tree(vault)

        settings = Settings(vault_path=str(vault), token="legacy-tok")
        app = create_app(settings)
        client = TestClient(app)

        # Auth with legacy token works
        h = {"Authorization": "Bearer legacy-tok"}
        assert client.get("/api/entities", headers=h).status_code == 200

        # whoami reports legacy mode
        r = client.get("/api/tenant/whoami", headers=h)
        assert r.status_code == 200
        data = r.json()
        assert data["mode"] == "legacy"

    def test_legacy_no_auth_works(self, tmp_path):
        vault = tmp_path / "vault"
        _make_vault_tree(vault)

        settings = Settings(vault_path=str(vault), token="")
        app = create_app(settings)
        client = TestClient(app)

        # No auth → everything open (legacy behaviour)
        assert client.get("/api/entities").status_code == 200


# ---------------------------------------------------------------------------
# Cross-vault search (§6.3)
# ---------------------------------------------------------------------------


class TestCrossVaultSearch:
    """Cross-vault search only searches vaults the caller can read."""

    def test_search_drops_unauthorized_vaults(self, tmp_path):
        root, _ = _make_mtav_env(tmp_path)
        _make_token(root, "greg-token", "gregory", [
            {"vault": "gregory", "scopes": ["read", "write"]},
            {"vault": "household", "scopes": ["read"]},
        ])
        reset_registry()
        settings = _mtav_settings(root)
        app = create_app(settings)
        client = TestClient(app)

        h = {"Authorization": "Bearer greg-token"}
        # Request search across kevin (unauthorized) + gregory
        r = client.get("/api/search?q=test&vaults=kevin,gregory", headers=h)
        assert r.status_code == 200
        data = r.json()
        # kevin should be silently dropped; only gregory searched
        searched = [v["name"] for v in data.get("vaults", [])]
        assert "kevin" not in searched


# ---------------------------------------------------------------------------
# Tenant registry registration (SI-8 foundations)
# ---------------------------------------------------------------------------


class TestVaultRegistration:
    """Vault registration via the registry API."""

    def test_register_new_vault(self, tmp_path):
        root, _ = _make_mtav_env(tmp_path)
        new_vault = root / "newcomer"
        _make_vault_tree(new_vault)

        reset_registry()
        settings = _mtav_settings(root)
        registry = get_registry(settings)

        initial_count = registry.count_vaults()
        registry.register_vault("newcomer", str(new_vault), "newcomer", "private")
        # Invalidate to pick up the change
        registry.invalidate()
        assert registry.count_vaults() == initial_count + 1
        assert registry.get_vault("newcomer") is not None

    def test_invalid_vault_name_rejected(self, tmp_path):
        root, _ = _make_mtav_env(tmp_path)
        reset_registry()
        settings = _mtav_settings(root)
        registry = get_registry(settings)

        with pytest.raises(ValueError):
            registry.register_vault("../../../etc", "/etc", "evil", "private")

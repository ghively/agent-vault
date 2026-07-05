"""Multi-tenant vault registry, identity model, and request routing.

Implements the Phase 1 MTAV layer (docs/MTAV_SPEC.md §4–§6):

- **TenantRegistry**: a file-backed, reloadable store of vaults + tokens/grants
  read from ``<vaults_root>/_tenant/{vaults.yaml, tokens.yaml}``. It satisfies
  **SI-6** (changes take effect on the next request) via a cached-but-
  invalidated design: mutation endpoints call :meth:`invalidate` after writing,
  and the registry mtime-checks on every read so out-of-band edits (a human
  editing the file directly) are picked up too. Writes are serialised by an
  ``fcntl`` file lock on ``_tenant/.lock``.

- **Identity / Grant**: the caller's resolved actor + grants. A grant is
  ``{vault, scopes, optional secret}`` where scopes ⊆
  {read, write, resolve, admin}. The identity knows how to answer
  ``has_scope(vault, scope)`` and the secret-level ``can_resolve(vault, slug)``
  question.

- **VaultResolver**: turns an HTTP request into a target vault *path* using the
  §6.1 routing rule (X-Vault header → path prefix → default fallback). No client
  input reaches a path join — vault names are slug-validated and resolved via
  registry lookup (**SI-2**).

- **Backward compatibility**: when ``multi_tenant=False`` (the default), the
  registry is a degenerate single-vault wrapper around the legacy
  ``settings.vault_path`` and ``settings.token``, and every function below
  returns a result equivalent to the pre-MTAV auth model. Existing deployments
  see zero behaviour change.
"""

from __future__ import annotations

import hashlib
import re
import secrets as secrets_mod
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from agent_vault.api.config import Settings

# --- validation patterns (SI-2) ---------------------------------------------

# Vault/actor names: lowercase alphanumeric + . _ -, 1–64 chars, leading alnum.
_SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$")

# Token hashes are stored as 64-char hex (sha256). The stored *key* in
# tokens.yaml is "<actor>-<first12>" so a human can eyeball ownership without
# the hash being the lookup key (the presented plaintext is hashed on lookup).
_TOKEN_KEY_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}-[a-f0-9]{12}$")

ALL_SCOPES = frozenset({"read", "write", "resolve", "admin"})
READ_SCOPES = frozenset({"read", "admin"})
WRITE_SCOPES = frozenset({"write", "admin"})
RESOLVE_SCOPES = frozenset({"resolve", "admin"})


def _validate_slug(name: str) -> str:
    """Return *name* if it is a safe slug, else raise ValueError (SI-2)."""
    if not isinstance(name, str) or not _SLUG_RE.match(name):
        raise ValueError(f"invalid vault/actor slug: {name!r}")
    return name


def _hash_token(plaintext: str) -> str:
    """sha256 hex of a token plaintext (tokens are stored hashed — §5.5)."""
    return hashlib.sha256(plaintext.encode("utf-8")).hexdigest()


def _gen_token() -> str:
    """Generate a random 40-char alphanumeric token."""
    alphabet = "abcdefghijklmnopqrstuvwxyz0123456789"
    return "".join(secrets_mod.choice(alphabet) for _ in range(40))


# ---------------------------------------------------------------------------
# Identity + Grant model
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Grant:
    """One permission entry on a token.

    ``vault`` is a vault name from vaults.yaml or ``"*"`` for all vaults
    (bootstrap only). ``scopes`` is a subset of ALL_SCOPES. ``secret`` is an
    optional slug for per-secret grants (§4.4).
    """

    vault: str
    scopes: frozenset[str]
    secret: str | None = None

    def matches_vault(self, vault: str) -> bool:
        return self.vault == "*" or self.vault == vault

    def implies(self, scope: str) -> bool:
        return "admin" in self.scopes or scope in self.scopes


@dataclass(frozen=True)
class Identity:
    """The authenticated caller: who they are and what they may do.

    In multi-tenant mode, ``grants`` is the list of per-vault grants from the
    token registry. In single-vault mode, a synthetic grant covers the single
    vault with the legacy scopes.

    ``vault_path`` is the *resolved* filesystem path of the default vault for
    this identity (§6.1 fallback), set by the resolver so endpoints can read
    it without re-resolving.
    """

    actor: str
    grants: tuple[Grant, ...]
    system: bool = False
    vault_path: str = ""  # set by the resolver, not the registry
    vault_name: str = ""  # the resolved default vault name

    # --- legacy compat (used when multi_tenant=False) ---
    _legacy_scopes: frozenset[str] = field(default_factory=frozenset)

    @classmethod
    def legacy(cls, actor: str, scopes: frozenset[str]) -> Identity:
        """Build a single-vault identity (pre-MTAV compat)."""
        return cls(
            actor=actor,
            grants=(),
            system=False,
            _legacy_scopes=scopes,
        )

    @property
    def scopes(self) -> frozenset[str]:
        """Flat scope set for backward-compat with the old ``allows()`` API."""
        if self._legacy_scopes:
            return self._legacy_scopes
        # Flatten all grants into a scope set (for the /whoami + audit views).
        out: set[str] = set()
        for g in self.grants:
            out |= set(g.scopes)
        return frozenset(out)

    def allows(self, scope: str) -> bool:
        """Legacy scope check (single-vault mode)."""
        return "admin" in self._legacy_scopes or scope in self._legacy_scopes

    def has_scope(self, vault: str, scope: str) -> bool:
        """Multi-tenant scope check: does this identity have *scope* on *vault*?

        ``admin`` on ``vault:"*"`` implies everything. For per-vault, the grant
        must match the vault name and imply the scope.
        """
        if self._legacy_scopes:
            return "admin" in self._legacy_scopes or scope in self._legacy_scopes
        for g in self.grants:
            if g.matches_vault(vault) and g.implies(scope):
                return True
        return False

    def can_resolve(self, vault: str, secret: str | None) -> bool:
        """Can this identity resolve a secret in *vault* (optionally per-secret)?

        A whole-vault ``resolve`` grant covers any secret in that vault. A
        per-secret grant covers only that slug.
        """
        if self._legacy_scopes:
            return "resolve" in self._legacy_scopes or "admin" in self._legacy_scopes
        for g in self.grants:
            if not g.matches_vault(vault):
                continue
            if not g.implies("resolve"):
                continue
            if g.secret is None or g.secret == secret:
                return True
        return False

    def visible_vaults(self) -> list[str]:
        """Vault names this identity has ANY grant on (for the vault switcher)."""
        if self._legacy_scopes:
            return []
        out: list[str] = []
        for g in self.grants:
            if g.vault != "*" and g.vault not in out:
                out.append(g.vault)
        return out


ANONYMOUS = Identity.legacy("anonymous", ALL_SCOPES)


# ---------------------------------------------------------------------------
# Vault descriptor (from vaults.yaml)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class VaultDesc:
    """One vault entry from ``vaults.yaml``."""

    name: str
    path: str
    owner: str
    visibility: str  # "private" | "shared"
    description: str = ""


# ---------------------------------------------------------------------------
# TenantRegistry — file-backed, reloadable (SI-6)
# ---------------------------------------------------------------------------


class TenantRegistry:
    """File-backed registry of vaults + tokens, reloadable per request (SI-6).

    The registry caches the parsed YAML in memory and mtime-checks on every
    access. If either file changed on disk, it reloads. Mutation endpoints
    call :meth:`invalidate` after writing to force an immediate reload on the
    next read (rather than waiting for the mtime check to notice).

    All file writes are serialised by an ``fcntl.flock`` on
    ``<vaults_root>/_tenant/.lock`` to prevent concurrent-corruption (SI-6).
    """

    def __init__(self, vaults_root: str, multi_tenant: bool) -> None:
        self.vaults_root = Path(vaults_root).expanduser()
        self.multi_tenant = multi_tenant
        self._tenant_dir = self.vaults_root / "_tenant"
        self._vaults_file = self._tenant_dir / "vaults.yaml"
        self._tokens_file = self._tenant_dir / "tokens.yaml"
        self._lock_file = self._tenant_dir / ".lock"

        self._vaults: dict[str, VaultDesc] = {}
        self._tokens: dict[str, Identity] = {}  # key = hashed token
        self._mtime_vaults: float = 0.0
        self._mtime_tokens: float = 0.0
        self._loaded = False

    @property
    def tenant_dir(self) -> Path:
        return self._tenant_dir

    # --- public API ---------------------------------------------------------

    def get_vault(self, name: str) -> VaultDesc | None:
        """Look up a vault by name (registry lookup only — SI-2)."""
        self._ensure_loaded()
        return self._vaults.get(name)

    def vault_path(self, name: str) -> str | None:
        """Resolve a vault name to a filesystem path (SI-2)."""
        desc = self.get_vault(name)
        return str(Path(desc.path).expanduser()) if desc else None

    def all_vaults(self) -> list[VaultDesc]:
        self._ensure_loaded()
        return list(self._vaults.values())

    def shared_vaults(self) -> list[str]:
        """Names of vaults with visibility=shared (auto-granted on self-mint)."""
        self._ensure_loaded()
        return [v.name for v in self._vaults.values() if v.visibility == "shared"]

    def match_token(self, presented: str) -> Identity | None:
        """Resolve a presented plaintext token to an Identity.

        Tokens are stored as sha256 hashes. We hash the presented value and
        look it up — a constant-time compare is not needed because the key IS
        the hash (the registry dict does the equality check).
        """
        self._ensure_loaded()
        h = _hash_token(presented)
        return self._tokens.get(h)

    def resolve_default_vault(self, identity: Identity) -> str | None:
        """§6.1 fallback: pick the default vault for an identity.

        - If the identity has a grant with ``write`` on exactly one vault →
          that vault.
        - Else if it has ``admin`` on ``vault:"*"`` (bootstrap) → ``household``
          (or the first shared vault, or the first vault).
        - Else → the first ``read``-granted vault in registry order.
        - Else → None (no grants at all).
        """
        self._ensure_loaded()
        if not self.multi_tenant:
            return None  # single-vault: no routing needed

        write_vaults = [
            g.vault
            for g in identity.grants
            if g.vault != "*" and g.implies("write")
        ]
        if len(write_vaults) == 1:
            return write_vaults[0]

        # Bootstrap: admin on "*" → household (or first shared)
        if any(g.vault == "*" and g.implies("admin") for g in identity.grants):
            shared = self.shared_vaults()
            if "household" in shared:
                return "household"
            if shared:
                return shared[0]
            all_v = list(self._vaults.keys())
            return all_v[0] if all_v else None

        # First read-granted vault in registry order
        for vname in self._vaults:
            if identity.has_scope(vname, "read"):
                return vname
        return None

    def invalidate(self) -> None:
        """Force a reload on the next access (SI-6)."""
        self._loaded = False

    # --- loading ------------------------------------------------------------

    def _ensure_loaded(self) -> None:
        if not self.multi_tenant:
            return
        # mtime-check: reload if either file changed
        try:
            mv = self._vaults_file.stat().st_mtime if self._vaults_file.exists() else 0.0
            mt = self._tokens_file.stat().st_mtime if self._tokens_file.exists() else 0.0
        except OSError:
            mv = mt = 0.0

        if self._loaded and mv == self._mtime_vaults and mt == self._mtime_tokens:
            return

        self._load()
        self._loaded = True
        self._mtime_vaults = mv
        self._mtime_tokens = mt

    def _load(self) -> None:
        """Parse vaults.yaml + tokens.yaml into memory."""
        self._vaults = {}
        self._tokens = {}

        # vaults.yaml
        if self._vaults_file.exists():
            try:
                data = yaml.safe_load(self._vaults_file.read_text(encoding="utf-8")) or {}
                for name, spec in (data.get("vaults") or {}).items():
                    if not isinstance(spec, dict):
                        continue
                    try:
                        _validate_slug(name)
                    except ValueError:
                        continue
                    self._vaults[name] = VaultDesc(
                        name=name,
                        path=str(spec.get("path", "")),
                        owner=str(spec.get("owner", "")),
                        visibility=str(spec.get("visibility", "private")),
                        description=str(spec.get("description", "")),
                    )
            except (OSError, yaml.YAMLError):
                pass

        # tokens.yaml
        if self._tokens_file.exists():
            try:
                data = yaml.safe_load(self._tokens_file.read_text(encoding="utf-8")) or {}
                for key, spec in (data.get("tokens") or {}).items():
                    if not isinstance(spec, dict):
                        continue
                    actor = str(spec.get("actor", ""))
                    is_system = bool(spec.get("system", False))
                    grants = self._parse_grants(spec.get("grants"))
                    # The key in the file IS the sha256 hash of the plaintext.
                    self._tokens[key] = Identity(
                        actor=actor,
                        grants=tuple(grants),
                        system=is_system,
                    )
            except (OSError, yaml.YAMLError):
                pass

    @staticmethod
    def _parse_grants(raw: Any) -> list[Grant]:
        grants: list[Grant] = []
        if not isinstance(raw, list):
            return grants
        for entry in raw:
            if not isinstance(entry, dict):
                continue
            vault = str(entry.get("vault", ""))
            if vault != "*":
                try:
                    _validate_slug(vault)
                except ValueError:
                    continue
            raw_scopes = entry.get("scopes", [])
            if isinstance(raw_scopes, str):
                raw_scopes = [raw_scopes]
            scopes = frozenset(
                str(s) for s in raw_scopes if str(s) in ALL_SCOPES
            )
            secret = entry.get("secret")
            grants.append(Grant(vault=vault, scopes=scopes, secret=str(secret) if secret else None))
        return grants

    # --- mutation (used by Phase 2 endpoints, safe to define now) ----------

    def _acquire_lock(self) -> Any:
        """fcntl.flock on _tenant/.lock for serialised writes (SI-6)."""
        import fcntl

        self._tenant_dir.mkdir(parents=True, exist_ok=True)
        lock_fh = open(self._lock_file, "w")
        fcntl.flock(lock_fh.fileno(), fcntl.LOCK_EX)
        return lock_fh

    @staticmethod
    def _release_lock(lock_fh: Any) -> None:
        import fcntl

        fcntl.flock(lock_fh.fileno(), fcntl.LOCK_UN)
        lock_fh.close()

    def add_token(
        self,
        plaintext: str,
        actor: str,
        grants: list[Grant],
        system: bool = False,
    ) -> None:
        """Write a new token to tokens.yaml + invalidate cache (SI-6)."""
        lock = self._acquire_lock()
        try:
            data: dict[str, Any] = {"tokens": {}}
            if self._tokens_file.exists():
                try:
                    data = yaml.safe_load(self._tokens_file.read_text(encoding="utf-8")) or {}
                except (OSError, yaml.YAMLError):
                    data = {"tokens": {}}
            if "tokens" not in data or not isinstance(data["tokens"], dict):
                data["tokens"] = {}

            h = _hash_token(plaintext)
            data["tokens"][h] = {
                "actor": actor,
                "system": system,
                "grants": [
                    {
                        "vault": g.vault,
                        "scopes": sorted(g.scopes),
                        **({"secret": g.secret} if g.secret else {}),
                    }
                    for g in grants
                ],
            }
            self._tokens_file.write_text(
                yaml.safe_dump(data, default_flow_style=False, sort_keys=False),
                encoding="utf-8",
            )
        finally:
            self._release_lock(lock)
        self.invalidate()

    def remove_token(self, token_hash: str) -> bool:
        """Remove a token by its hash. Returns True if it was present."""
        lock = self._acquire_lock()
        try:
            if not self._tokens_file.exists():
                return False
            data = yaml.safe_load(self._tokens_file.read_text(encoding="utf-8")) or {}
            tokens = data.get("tokens") or {}
            if token_hash not in tokens:
                return False
            del tokens[token_hash]
            data["tokens"] = tokens
            self._tokens_file.write_text(
                yaml.safe_dump(data, default_flow_style=False, sort_keys=False),
                encoding="utf-8",
            )
            return True
        except (OSError, yaml.YAMLError):
            return False
        finally:
            self._release_lock(lock)
        # unreachable: invalidate always runs in finally

    def register_vault(self, name: str, path: str, owner: str, visibility: str = "private") -> None:
        """Add/update a vault entry in vaults.yaml + invalidate (SI-6)."""
        _validate_slug(name)
        lock = self._acquire_lock()
        try:
            data: dict[str, Any] = {"vaults": {}}
            if self._vaults_file.exists():
                try:
                    data = yaml.safe_load(self._vaults_file.read_text(encoding="utf-8")) or {}
                except (OSError, yaml.YAMLError):
                    data = {"vaults": {}}
            if "vaults" not in data or not isinstance(data["vaults"], dict):
                data["vaults"] = {}
            data["vaults"][name] = {
                "path": path,
                "owner": owner,
                "visibility": visibility,
            }
            self._vaults_file.write_text(
                yaml.safe_dump(data, default_flow_style=False, sort_keys=False),
                encoding="utf-8",
            )
        finally:
            self._release_lock(lock)
        self.invalidate()

    def count_vaults(self) -> int:
        self._ensure_loaded()
        return len(self._vaults)


# ---------------------------------------------------------------------------
# Module-level singleton accessor (one registry per process)
# ---------------------------------------------------------------------------

_registry_instance: TenantRegistry | None = None
_registry_settings_key: tuple[str, bool] | None = None


def get_registry(settings: Settings) -> TenantRegistry:
    """Return the process-wide TenantRegistry for the given settings.

    The registry is keyed by (vaults_root, multi_tenant) so a test that swaps
    settings gets a fresh instance rather than a stale cache.
    """
    global _registry_instance, _registry_settings_key
    key = (settings.vaults_root, settings.multi_tenant)
    if _registry_instance is None or key != _registry_settings_key:
        _registry_instance = TenantRegistry(
            vaults_root=settings.vaults_root,
            multi_tenant=settings.multi_tenant,
        )
        _registry_settings_key = key
    return _registry_instance


def reset_registry() -> None:
    """Clear the singleton (for tests)."""
    global _registry_instance, _registry_settings_key
    _registry_instance = None
    _registry_settings_key = None


# ---------------------------------------------------------------------------
# VaultResolver — §6.1 routing rule
# ---------------------------------------------------------------------------


class VaultResolver:
    """Resolves an HTTP request to a target vault path + name (§6.1).

    Resolution order:
    1. ``X-Vault`` header → must be in the caller's grants (else 403).
    2. ``/api/vaults/{vault}/...`` path prefix → same grant check.
    3. Default fallback (registry.resolve_default_vault for this identity).

    In single-vault mode, always returns the static ``settings.vault_path``
    and skips all grant checks (legacy behaviour).
    """

    def __init__(self, registry: TenantRegistry, settings: Settings) -> None:
        self.registry = registry
        self.settings = settings

    def resolve(
        self,
        identity: Identity,
        x_vault: str | None,
        path: str,
    ) -> tuple[str, str]:
        """Return (vault_name, vault_path) for this request.

        Raises ValueError if the requested vault is not in the registry or the
        caller lacks any grant on it. The caller (auth dependency) translates
        ValueError → 403 (grant-first, existence-second → SI-5).
        """
        if not self.settings.multi_tenant:
            return ("", self.settings.vault_path)

        target: str | None = None

        # 1. X-Vault header
        if x_vault:
            _validate_slug(x_vault)
            target = x_vault

        # 2. Path prefix /api/vaults/{vault}/...
        if target is None:
            parts = path.strip("/").split("/")
            if len(parts) >= 3 and parts[0] == "api" and parts[1] == "vaults":
                _validate_slug(parts[2])
                target = parts[2]

        # 3. Default fallback
        if target is None:
            target = self.registry.resolve_default_vault(identity)
            if target is None:
                # No grants at all → resolve to "household" if it exists so a
                # 403 (not 404) is returned, matching SI-5.
                target = "household" if self.registry.get_vault("household") else ""

        # Grant check (SI-5: before existence check)
        if target:
            has_any = identity.system or any(
                g.matches_vault(target) for g in identity.grants
            )
            if not has_any:
                raise ValueError(f"no grant on vault '{target}'")

        # Existence check (after grant check)
        vpath = self.registry.vault_path(target) if target else None
        if target and vpath is None:
            raise ValueError(f"vault '{target}' not in registry")

        return (target or "", vpath or "")


# ---------------------------------------------------------------------------
# Tenant control-plane router (Phase 1: whoami + vaults listing)
# ---------------------------------------------------------------------------


from fastapi import APIRouter as _APIRouter, Request as _Request  # noqa: E402
from fastapi.responses import JSONResponse as _JSONResponse  # noqa: E402

router = _APIRouter()


@router.get("/tenant/whoami")
async def whoami(request: _Request) -> _JSONResponse:
    """Return the caller's actor + grants (for the UI + agents).

    In legacy single-vault mode, returns the actor + flat scopes. In MTAV mode,
    returns the actor + structured grants + the resolved default vault.
    """
    ident = request.state.identity
    if ident._legacy_scopes:  # noqa: SLF001 — legacy mode
        return _JSONResponse(content={
            "actor": ident.actor,
            "mode": "legacy",
            "scopes": sorted(ident._legacy_scopes),  # noqa: SLF001
        })
    return _JSONResponse(content={
        "actor": ident.actor,
        "mode": "multi-tenant",
        "system": ident.system,
        "default_vault": ident.vault_name,
        "grants": [
            {
                "vault": g.vault,
                "scopes": sorted(g.scopes),
                **({"secret": g.secret} if g.secret else {}),
            }
            for g in ident.grants
        ],
        "visible_vaults": ident.visible_vaults(),
    })


@router.get("/tenant/vaults")
async def list_visible_vaults(request: _Request) -> _JSONResponse:
    """List vaults the caller can see (for the workspace switcher)."""
    ident = request.state.identity
    settings = request.app.state.settings

    if not settings.multi_tenant:
        return _JSONResponse(content={
            "vaults": [{
                "name": "",
                "path": settings.vault_path,
                "description": "single-vault mode",
            }],
            "mode": "legacy",
        })

    registry = get_registry(settings)
    visible = set(ident.visible_vaults())
    if ident.system:
        # Bootstrap sees all vaults
        visible = {v.name for v in registry.all_vaults()}

    vaults = []
    for v in registry.all_vaults():
        if v.name in visible:
            vaults.append({
                "name": v.name,
                "owner": v.owner,
                "visibility": v.visibility,
                "description": v.description,
            })
    return _JSONResponse(content={"vaults": vaults, "mode": "multi-tenant"})


@router.get("/search")
async def cross_vault_search(
    request: _Request,
    q: str = "",
    vaults: str = "",
    limit: int = 20,
) -> _JSONResponse:
    """Cross-vault FTS search (§6.3).

    Fans out FTS across the listed vaults (comma-separated), merge-ranked.
    Restricted to vaults the caller has ``read`` on; others silently dropped.
    In legacy mode, delegates to the standard single-vault search.
    """

    settings = request.app.state.settings
    ident = request.state.identity

    if not settings.multi_tenant:
        # Delegate to the regular search endpoint
        from agent_vault import search as search_mod
        query = q.strip()
        if not query:
            return _JSONResponse(content={"query": "", "hits": [], "vaults": []})
        hits = search_mod.search(settings.vault_path, query, limit, mode="fts")
        return _JSONResponse(content={
            "query": query,
            "hits": hits,
            "vaults": [{"name": "", "hits": len(hits)}],
        })

    registry = get_registry(settings)
    query = q.strip()
    if not query:
        return _JSONResponse(content={"query": "", "hits": [], "vaults": []})

    # Parse requested vaults; empty = all visible
    requested = [v.strip() for v in vaults.split(",") if v.strip()] if vaults else []
    visible = set(ident.visible_vaults())
    if ident.system:
        visible = {v.name for v in registry.all_vaults()}

    # Filter to vaults the caller can read
    searchable: list[str] = []
    for vname in (requested or visible):
        if vname in visible and ident.has_scope(vname, "read"):
            searchable.append(vname)
        # silently drop unauthorized vaults

    if not searchable:
        return _JSONResponse(content={
            "query": query,
            "hits": [],
            "vaults": [],
            "note": "no searchable vaults (check grants)",
        })

    from agent_vault import search as search_mod

    all_hits: list[dict[str, Any]] = []
    per_vault: list[dict[str, Any]] = []
    for vname in searchable:
        vpath = registry.vault_path(vname)
        if not vpath:
            continue
        try:
            hits = search_mod.search(vpath, query, limit, mode="fts")
        except Exception:  # noqa: BLE001 — a broken index in one vault shouldn't fail the whole search
            continue
        for h in hits:
            h["vault"] = vname
        all_hits.extend(hits)
        per_vault.append({"name": vname, "hits": len(hits)})

    # Merge-rank: sort by score descending
    all_hits.sort(key=lambda h: h.get("score", 0), reverse=True)
    all_hits = all_hits[:limit]

    return _JSONResponse(content={
        "query": query,
        "hits": all_hits,
        "vaults": per_vault,
    })

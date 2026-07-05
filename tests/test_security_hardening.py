"""Regression tests for two security hardening fixes (MoA-verified).

1. Resolver backend import must NOT pollute sys.path with the vault root — a
   write-scoped agent dropping ``<vault>/whatever.py`` must not shadow stdlib or
   installed packages (write→RCE pivot). Only ``resolvers/<name>.py`` may load
   as a vault-local resolver backend.

2. Denied auth (401/403) must still attribute the caller in the audit log
   instead of collapsing to ``actor="anonymous"``.
"""

import json
import os
import sys
import tempfile
from pathlib import Path

from fastapi.testclient import TestClient

from agent_vault.api.app import create_app
from agent_vault.api.config import Settings
from agent_vault.resolvers import _import_backend


def _vault(resolvers_subdir: bool = True) -> str:
    """A throwaway vault with registry/ + discovery/ (+ resolvers/)."""
    d = tempfile.mkdtemp()
    os.makedirs(f"{d}/registry")
    os.makedirs(f"{d}/discovery")
    if resolvers_subdir:
        os.makedirs(f"{d}/resolvers")
    return d


# --------------------------------------------------------------------------- #
# Finding 7: sys.path RCE pivot closed
# --------------------------------------------------------------------------- #
def test_vault_root_py_does_not_shadow_installed_packages():
    """A file at <vault>/yaml.py must not shadow the installed PyYAML."""
    d = _vault()
    with open(f"{d}/yaml.py", "w") as f:
        f.write('raise RuntimeError("shadowed yaml loaded — RCE pivot open")\n')
    # Trigger a resolver import (the old vector) against the vault.
    _import_backend("agent_vault.resolvers.env", "env", d)
    # The real yaml must still import and be the installed one, not the vault file.
    import importlib
    import yaml

    importlib.reload(yaml)
    assert yaml.__file__ is not None
    assert os.path.abspath(yaml.__file__).startswith(os.path.abspath(d) + os.sep) is False, (
        "vault-root yaml.py shadowed the installed package — RCE pivot open"
    )
    # And the vault dir must never have entered sys.path.
    assert os.path.abspath(d) not in sys.path


def test_vault_root_py_does_not_shadow_stdlib():
    """A file at <vault>/json.py must not shadow stdlib json."""
    d = _vault()
    with open(f"{d}/json.py", "w") as f:
        f.write('raise RuntimeError("shadowed json loaded — RCE pivot open")\n')
    _import_backend("agent_vault.resolvers.env", "env", d)
    # Re-importing json from a fresh interpreter state isn't trivial; instead
    # just confirm the vault dir never made it onto sys.path (the mechanism).
    assert os.path.abspath(d) not in sys.path
    # And a module that hasn't been imported yet still resolves to the stdlib.
    # We use 'csv' (unlikely to be already imported by the resolver path) as a
    # clean proxy: if sys.path[0] were the vault, a csv.py there would win.
    import csv

    assert os.path.abspath(csv.__file__).startswith(os.path.abspath(d)) is False


def test_vault_local_resolver_extension_still_loads():
    """The documented resolvers/<name>.py extension pattern still works."""
    d = _vault()
    with open(f"{d}/resolvers/custom.py", "w") as f:
        f.write("def resolve(ref, config):\n    return 'custom-value'\n")
    mod = _import_backend("resolvers.custom", "custom", d)
    assert mod.resolve.__module__ == "resolvers.custom"
    assert mod.resolve(None, {}) == "custom-value"


def test_vault_local_resolver_cached_across_calls():
    """A vault-local backend loads once, not re-read on every resolve call."""
    d = _vault()
    with open(f"{d}/resolvers/custom.py", "w") as f:
        f.write("def resolve(ref, config):\n    return 'custom-value'\n")
    m1 = _import_backend("resolvers.custom", "custom", d)
    m2 = _import_backend("resolvers.custom", "custom", d)
    assert m1 is m2, "vault-local resolver was re-loaded instead of cached"


def test_vault_local_resolver_syntax_error_raises_resolvererror():
    """A broken resolvers/<name>.py surfaces as a clean ResolverError, not a
    raw traceback that crashes the CLI / API caller."""
    d = _vault()
    with open(f"{d}/resolvers/broken.py", "w") as f:
        f.write("def resolve(:\n    pass\n")  # SyntaxError
    from agent_vault.resolvers import ResolverError
    import pytest

    with pytest.raises(ResolverError) as exc_info:
        _import_backend("resolvers.broken", "broken", d)
    assert "failed to load" in str(exc_info.value)
    # The broken module must not linger in sys.modules.
    assert "resolvers.broken" not in sys.modules


def test_resolver_dotted_name_with_traversal_is_not_loaded_from_vault():
    """A resolvers. leaf that isn't a plain identifier can't target a path."""
    d = _vault()
    # Leaf "..foo" is not a valid identifier -> falls through to normal import,
    # which raises ImportError (wrapped as ResolverError by the caller).
    import pytest
    from agent_vault.resolvers import ResolverError

    with pytest.raises((ImportError, ResolverError)):
        _import_backend("resolvers..foo", "foo", d)


# --------------------------------------------------------------------------- #
# Finding 1: audit attribution on denied requests
# --------------------------------------------------------------------------- #
def _audit_actors(d: str) -> list[str]:
    p = Path(d) / "discovery" / "_access.jsonl"
    if not p.exists():
        return []
    return [json.loads(ln)["actor"] for ln in p.read_text().splitlines() if ln.strip()]


def test_audit_attributes_missing_token_denial():
    d = _vault()
    settings = Settings(host="127.0.0.1", port=7778, vault_path=d, token="correct")
    client = TestClient(create_app(settings))
    # A mutating call with no token -> 401, audited with a real actor.
    r = client.post(
        "/api/entities/x/reclassify", json={"to_type": "account", "to_subtype": "checking"}
    )
    assert r.status_code == 401
    actors = _audit_actors(d)
    assert actors, "denial was not audited at all"
    assert "anonymous" not in actors, "missing-token denial logged as anonymous"
    assert "unknown" in actors, "missing-token denial not attributed"


def test_audit_attributes_bad_token_denial():
    d = _vault()
    settings = Settings(host="127.0.0.1", port=7778, vault_path=d, token="correct")
    client = TestClient(create_app(settings))
    r = client.post(
        "/api/entities/x/reclassify",
        json={"to_type": "account", "to_subtype": "checking"},
        headers={"Authorization": "Bearer wrong-token"},
    )
    assert r.status_code == 401
    actors = _audit_actors(d)
    assert "anonymous" not in actors
    assert any(a.startswith("token:") for a in actors), "bad-token denial not hashed-attributed"


def test_audit_bad_token_actor_is_stable_hash_not_plaintext():
    """Two attempts with the same bad token attribute to the same tag, and the
    tag never contains the token itself."""
    d = _vault()
    settings = Settings(host="127.0.0.1", port=7778, vault_path=d, token="correct")
    client = TestClient(create_app(settings))
    for _ in range(2):
        client.post(
            "/api/entities/x/reclassify",
            json={"to_type": "account", "to_subtype": "checking"},
            headers={"Authorization": "Bearer supersecret-bad"},
        )
    actors = [a for a in _audit_actors(d) if a.startswith("token:")]
    assert len(actors) == 2
    assert actors[0] == actors[1], "same bad token produced different tags"
    assert "supersecret-bad" not in actors[0], "plaintext token leaked into audit actor"


def test_audit_attributes_wrong_scope_denial_to_real_actor():
    """A valid token that lacks the required scope (403) must be attributed to
    the real caller in the audit log, not collapsed to 'anonymous'."""
    import json as _json

    d = _vault()
    # Per-agent token registry via VAULT_TOKENS: 'reader' has only read scope.
    os.environ["VAULT_TOKENS"] = _json.dumps(
        {"reader-token": {"actor": "reader", "scopes": ["read"]}}
    )
    try:
        settings = Settings(host="127.0.0.1", port=7778, vault_path=d, token="")
        client = TestClient(create_app(settings))
        # A write with a read-only token -> 403, audited as actor='reader'.
        r = client.post(
            "/api/entities/x/reclassify",
            json={"to_type": "account", "to_subtype": "checking"},
            headers={"Authorization": "Bearer reader-token"},
        )
        assert r.status_code == 403
        actors = _audit_actors(d)
        assert "reader" in actors, "403 (wrong scope) not attributed to real actor"
        assert "anonymous" not in actors
    finally:
        os.environ.pop("VAULT_TOKENS", None)

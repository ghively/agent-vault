"""Tests for credential resolve and entity recompile endpoints."""

import json
import tempfile
from pathlib import Path

from fastapi.testclient import TestClient

from agent_vault.api.app import create_app
from agent_vault.api.config import Settings


def create_test_vault_with_resolver() -> Path:
    """Create a minimal test vault with a stub resolver backend."""
    vault_dir = tempfile.mkdtemp()
    vault = Path(vault_dir)

    # Create directory structure
    (vault / "entities" / "account").mkdir(parents=True)
    (vault / "discovery").mkdir(parents=True)
    (vault / "registry").mkdir(parents=True)
    (vault / "resolvers").mkdir(parents=True)

    # Entity with credential_ref
    entity_md = """---
slug: test-account
type: account
subtype: checking
title: Test Account
status: compiled
sources_hash: seed000001
credential_ref: test://banking/test-account
---

<!-- LINKS:BEGIN -->
<!-- LINKS:END -->

Test account entity.
"""
    (vault / "entities" / "account" / "test-account.md").write_text(entity_md)

    # Build index.json
    index_data = {
        "entities": [
            {
                "slug": "test-account",
                "type": "account",
                "subtype": "checking",
                "title": "Test Account",
                "status": "compiled",
                "path": "entities/account/test-account.md",
                "has_credential": True,
            }
        ]
    }
    (vault / "_index.json").write_text(json.dumps(index_data))

    # Create a test backend resolver module (the trusted agent_vault.resolvers
    # package will load this from the vault dir via its _import_backend function)
    test_backend = """\"\"\"Test resolver backend for testing.\"\"\"

def resolve(parsed, backend):
    \"\"\"Return a deterministic secret for testing.\"\"\"
    return f"test-secret-{parsed.store}-{parsed.path[0] if parsed.path else 'default'}"
"""
    (vault / "resolvers" / "test.py").write_text(test_backend)

    # Create resolvers.yaml registry
    resolvers_yaml = """
resolvers:
  test:
    module: resolvers.test
default_resolver: test
"""
    (vault / "registry" / "resolvers.yaml").write_text(resolvers_yaml)

    return vault


def create_test_vault_with_compilable_entity() -> Path:
    """Create a minimal test vault with a compilable entity."""
    vault_dir = tempfile.mkdtemp()
    vault = Path(vault_dir)

    # Create directory structure
    (vault / "entities" / "account").mkdir(parents=True)
    (vault / "discovery").mkdir(parents=True)
    (vault / "registry").mkdir(parents=True)

    # Entity in stub status (ready to compile)
    entity_md = """---
slug: stub-entity
type: account
subtype: savings
title: Stub Entity
status: stub
sources:
  - raw/test.txt
sources_hash: seed000002
---

<!-- LINKS:BEGIN -->
<!-- LINKS:END -->

This is a stub entity ready for compile.
"""
    (vault / "entities" / "account" / "stub-entity.md").write_text(entity_md)

    # Create a dummy source file
    (vault / "raw").mkdir(parents=True)
    (vault / "raw" / "test.txt").write_text("Test source content")

    # Build index.json
    index_data = {
        "entities": [
            {
                "slug": "stub-entity",
                "type": "account",
                "subtype": "savings",
                "title": "Stub Entity",
                "status": "stub",
                "path": "entities/account/stub-entity.md",
                "has_credential": False,
            }
        ]
    }
    (vault / "_index.json").write_text(json.dumps(index_data))

    # Create minimal schema.yaml
    schema_yaml = """
types:
  account:
    subtypes:
      - checking
      - savings
tags: {}
"""
    (vault / "registry" / "schema.yaml").write_text(schema_yaml)

    return vault


def test_resolve_credential_success():
    """Test POST /api/creds/{slug}/resolve returns the secret."""
    vault = create_test_vault_with_resolver()
    settings = Settings(vault_path=str(vault), token="")
    app = create_app(settings)
    client = TestClient(app)

    response = client.post("/api/creds/test-account/resolve")
    assert response.status_code == 200

    data = response.json()
    assert data["ok"] is True
    assert "secret" in data
    # The stub resolver returns deterministic secrets
    assert data["secret"] == "test-secret-banking-test-account"


def test_resolve_credential_invalid_slug():
    """Test POST /api/creds/{slug}/resolve returns 422 for invalid slug."""
    vault = create_test_vault_with_resolver()
    settings = Settings(vault_path=str(vault), token="")
    app = create_app(settings)
    client = TestClient(app)

    # Invalid slug (uppercase not allowed)
    response = client.post("/api/creds/InvalidSlug/resolve")
    assert response.status_code == 422


def test_resolve_credential_entity_not_found():
    """Test POST /api/creds/{slug}/resolve returns 404 for unknown entity."""
    vault = create_test_vault_with_resolver()
    settings = Settings(vault_path=str(vault), token="")
    app = create_app(settings)
    client = TestClient(app)

    response = client.post("/api/creds/nonexistent/resolve")
    assert response.status_code == 404


def test_resolve_credential_no_secret_in_logs():
    """Test that resolved secrets never appear in logs or captured output."""
    import io
    from contextlib import redirect_stderr, redirect_stdout

    vault = create_test_vault_with_resolver()
    settings = Settings(vault_path=str(vault), token="")
    app = create_app(settings)
    client = TestClient(app)

    # Capture stdout and stderr during the request
    stdout_capture = io.StringIO()
    stderr_capture = io.StringIO()

    with redirect_stdout(stdout_capture), redirect_stderr(stderr_capture):
        response = client.post("/api/creds/test-account/resolve")

    assert response.status_code == 200
    data = response.json()
    secret_value = data["secret"]

    # Verify the secret is NOT in any captured output
    stdout_output = stdout_capture.getvalue()
    stderr_output = stderr_capture.getvalue()

    assert secret_value not in stdout_output, "Secret leaked to stdout"
    assert secret_value not in stderr_output, "Secret leaked to stderr"

    # Also check that common secret-like patterns aren't in the output
    assert "password" not in stdout_output.lower()
    assert "password" not in stderr_output.lower()


def test_recompile_entity_success():
    """Test POST /api/entities/{slug}/recompile triggers compile under lock."""
    from unittest.mock import patch

    vault = create_test_vault_with_compilable_entity()
    settings = Settings(vault_path=str(vault), token="")
    app = create_app(settings)
    client = TestClient(app)

    # Mock the lock-free compile primitive the endpoint calls (see D1) to
    # avoid actual compilation in this shape-only test.
    mock_compile_result = {
        "client": "mock",
        "contract_version": "2.0",
        "compiled": [{"slug": "stub-entity", "ok": True}],
        "failed": [],
        "proposals_logged": 0
    }

    with patch('agent_vault.api.creds.compiler._compile_all_locked', return_value=mock_compile_result):
        # Trigger recompile
        response = client.post("/api/entities/stub-entity/recompile")
        assert response.status_code == 200

        data = response.json()
        assert data.get("compiled") or data.get("failed")

    # The entity should still exist (compile doesn't delete files)
    entity_path = vault / "entities" / "account" / "stub-entity.md"
    assert entity_path.exists()


def test_recompile_entity_real_compile_no_deadlock(monkeypatch):
    """Regression test for D1: the endpoint holds the vault write lock and then
    compiles. Before the fix it called compiler.compile_all(), which re-acquired
    the same non-reentrant flock and self-deadlocked until the lock timeout
    (default 600s) -> 503. This test runs the REAL compile path (no mock of the
    compiler) with the offline MockClient; if the deadlock ever returns it hangs
    and pytest-timeout (120s, set in pyproject.toml) fails it instead of a stall.
    """
    monkeypatch.setenv("AGENT_VAULT_COMPILER", "mock")
    # Keep the lock timeout short so a regression fails fast rather than after
    # the 600s default (still exercises the same acquire path).
    monkeypatch.setenv("AGENT_VAULT_LOCK_TIMEOUT_S", "5")

    vault = create_test_vault_with_compilable_entity()
    settings = Settings(vault_path=str(vault), token="")
    app = create_app(settings)
    client = TestClient(app)

    response = client.post("/api/entities/stub-entity/recompile")
    assert response.status_code == 200, response.text

    data = response.json()
    # The stub was eligible, so the real MockClient pass should have compiled it.
    assert data.get("client", "").startswith("mock")
    compiled_slugs = {c.get("slug") for c in data.get("compiled", [])}
    assert "stub-entity" in compiled_slugs, data

    # The on-disk entity must end in a coherent state: compiled, with prose,
    # never left as the transient stub-with-prose the pre-fix failure produced.
    text = (vault / "entities" / "account" / "stub-entity.md").read_text()
    assert "status: compiled" in text


def test_recompile_entity_invalid_slug():
    """Test POST /api/entities/{slug}/recompile returns 422 for invalid slug."""
    vault = create_test_vault_with_compilable_entity()
    settings = Settings(vault_path=str(vault), token="")
    app = create_app(settings)
    client = TestClient(app)

    # Invalid slug - should return 422 before compiler is called
    response = client.post("/api/entities/Invalid_Slug/recompile")
    assert response.status_code == 422


def test_recompile_entity_not_found():
    """Test POST /api/entities/{slug}/recompile returns 404 for unknown entity."""
    vault = create_test_vault_with_compilable_entity()
    settings = Settings(vault_path=str(vault), token="")
    app = create_app(settings)
    client = TestClient(app)

    # Unknown entity - should return 404 before compiler is called
    response = client.post("/api/entities/nonexistent/recompile")
    assert response.status_code == 404


def test_creds_response_shape_matches_synapsenas():
    """Test that response shapes match SynapseNAS patterns."""
    from unittest.mock import patch

    vault = create_test_vault_with_resolver()
    settings = Settings(vault_path=str(vault), token="")
    app = create_app(settings)
    client = TestClient(app)

    # POST /api/creds/{slug}/resolve shape
    response = client.post("/api/creds/test-account/resolve")
    data = response.json()
    assert set(data.keys()) == {"ok", "secret"}
    assert isinstance(data["ok"], bool)
    assert isinstance(data["secret"], str)

    # POST /api/entities/{slug}/recompile shape
    vault2 = create_test_vault_with_compilable_entity()
    settings2 = Settings(vault_path=str(vault2), token="")
    app2 = create_app(settings2)
    client2 = TestClient(app2)

    # Mock the compiler for this test
    mock_compile_result = {
        "client": "mock",
        "compiled": [],
        "failed": [],
    }

    with patch('agent_vault.api.creds.compiler._compile_all_locked', return_value=mock_compile_result):
        response = client2.post("/api/entities/stub-entity/recompile")
        data = response.json()
        # Compile result structure (may vary based on compiler result)
        assert isinstance(data, dict)


def test_resolve_with_auth_token():
    """Test that resolve endpoint works with bearer token auth."""
    vault = create_test_vault_with_resolver()
    settings = Settings(vault_path=str(vault), token="test-token")
    app = create_app(settings)
    client = TestClient(app)

    # With correct token
    response = client.post(
        "/api/creds/test-account/resolve",
        headers={"Authorization": "Bearer test-token"}
    )
    assert response.status_code == 200

    # With wrong token
    response = client.post(
        "/api/creds/test-account/resolve",
        headers={"Authorization": "Bearer wrong-token"}
    )
    assert response.status_code == 401


def test_recompile_with_auth_token():
    """Test that recompile endpoint works with bearer token auth."""
    from unittest.mock import patch

    vault = create_test_vault_with_compilable_entity()
    settings = Settings(vault_path=str(vault), token="test-token")
    app = create_app(settings)
    client = TestClient(app)

    # Mock the compiler for this test
    mock_compile_result = {
        "client": "mock",
        "compiled": [],
        "failed": [],
    }

    with patch('agent_vault.api.creds.compiler._compile_all_locked', return_value=mock_compile_result):
        # With correct token
        response = client.post(
            "/api/entities/stub-entity/recompile",
            headers={"Authorization": "Bearer test-token"}
        )
        assert response.status_code == 200

        # With wrong token
        response = client.post(
            "/api/entities/stub-entity/recompile",
            headers={"Authorization": "Bearer wrong-token"}
        )
        assert response.status_code == 401

"""Tests for GET /api/config and POST /api/config/apply."""

import tempfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from agent_vault.api.app import create_app
from agent_vault.api.config import Settings

SCHEMA_YAML = """\
version: 1
types:
  account: {}
promotion:
  new_tag:      { auto: true,  min_sightings: 3 }
  new_subtype:  { auto: true,  min_sightings: 2, require_parent_exists: true }
  new_type:     { auto: false }
"""

RESOLVERS_YAML = """\
default_resolver: age
resolvers:
  age:
    module: resolvers.age_backend
    description: age-encrypted file store
  env:
    module: resolvers.env_backend
    description: environment variable lookup
"""


def create_test_vault() -> Path:
    """Create a minimal test vault with registry schema + resolvers."""
    vault_dir = tempfile.mkdtemp()
    vault = Path(vault_dir)
    (vault / "registry").mkdir(parents=True)
    (vault / "registry" / "schema.yaml").write_text(SCHEMA_YAML, encoding="utf-8")
    (vault / "registry" / "resolvers.yaml").write_text(RESOLVERS_YAML, encoding="utf-8")
    return vault


def _client() -> TestClient:
    vault = create_test_vault()
    settings = Settings(vault_path=str(vault), token="")
    return TestClient(create_app(settings))


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    """Keep the allowlisted env keys out of the test process environment."""
    for key in (
        "AGENT_VAULT_COMPILER",
        "AGENT_VAULT_PER_SOURCE_CHARS",
        "AGENT_VAULT_MAX_RAW_MB",
        "OLLAMA_MODEL",
    ):
        monkeypatch.delenv(key, raising=False)


def test_config_happy_path():
    """GET /api/config returns env defaults, schema thresholds, resolvers."""
    client = _client()
    response = client.get("/api/config")
    assert response.status_code == 200
    data = response.json()

    # env: the code's real defaults when nothing is set
    assert data["env"] == {
        "AGENT_VAULT_COMPILER": "ollama",
        "AGENT_VAULT_PER_SOURCE_CHARS": "4000",
        "AGENT_VAULT_MAX_RAW_MB": "50",
        "OLLAMA_MODEL": "qwen2.5:7b-instruct",
    }

    # thresholds from registry/schema.yaml's promotion: block
    assert data["thresholds"] == {
        "new_tag_min": 3,
        "new_subtype_min": 2,
        "new_type_locked": True,
    }

    # resolvers from registry/resolvers.yaml (scheme -> name, description -> detail)
    assert {"name": "age", "detail": "age-encrypted file store"} in data["resolvers"]
    assert {"name": "env", "detail": "environment variable lookup"} in data["resolvers"]


def test_config_missing_registry_files():
    """Config degrades gracefully in a bare vault."""
    vault = Path(tempfile.mkdtemp())
    client = TestClient(create_app(Settings(vault_path=str(vault), token="")))

    response = client.get("/api/config")
    assert response.status_code == 200
    data = response.json()
    assert data["thresholds"] == {
        "new_tag_min": None,
        "new_subtype_min": None,
        "new_type_locked": True,
    }
    assert data["resolvers"] == []


def test_config_apply_env_allowlisted(monkeypatch):
    """Applying allowlisted env keys updates os.environ (jobs inherit it)."""
    import os

    client = _client()
    response = client.post(
        "/api/config/apply",
        json={"env": {"AGENT_VAULT_COMPILER": "mock", "AGENT_VAULT_MAX_RAW_MB": "10"}},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["env"]["AGENT_VAULT_COMPILER"] == "mock"
    assert data["env"]["AGENT_VAULT_MAX_RAW_MB"] == "10"
    # untouched keys keep their defaults
    assert data["env"]["AGENT_VAULT_PER_SOURCE_CHARS"] == "4000"

    # os.environ was updated, so /api/jobs subprocess env (built from
    # os.environ in jobs._vault_env) propagates the change
    assert os.environ["AGENT_VAULT_COMPILER"] == "mock"
    assert os.environ["AGENT_VAULT_MAX_RAW_MB"] == "10"

    from agent_vault.api.jobs import _vault_env

    child_env = _vault_env(Path("."))
    assert child_env["AGENT_VAULT_COMPILER"] == "mock"

    # cleanup (autouse fixture deletes before each test; be tidy anyway)
    monkeypatch.delenv("AGENT_VAULT_COMPILER", raising=False)
    monkeypatch.delenv("AGENT_VAULT_MAX_RAW_MB", raising=False)


def test_config_apply_unknown_key_rejected():
    """Env keys outside the allowlist are a 400 — nothing is applied."""
    import os

    client = _client()
    response = client.post(
        "/api/config/apply",
        json={"env": {"PATH": "/tmp/evil", "AGENT_VAULT_COMPILER": "mock"}},
    )
    assert response.status_code == 400
    assert "PATH" in response.json()["detail"]
    assert os.environ.get("PATH") != "/tmp/evil"
    # the valid key in the same request must NOT have been applied either
    assert os.environ.get("AGENT_VAULT_COMPILER") != "mock"


def test_config_apply_invalid_values_rejected():
    """Allowlisted keys with invalid values are a 400."""
    client = _client()
    bad = [
        {"AGENT_VAULT_COMPILER": "gpt4"},  # not a known backend
        {"AGENT_VAULT_PER_SOURCE_CHARS": "0"},  # must be positive
        {"AGENT_VAULT_PER_SOURCE_CHARS": "-5"},
        {"AGENT_VAULT_MAX_RAW_MB": "999999999"},  # out of bounds
        {"OLLAMA_MODEL": "model; rm -rf /"},  # unsafe characters
    ]
    for env in bad:
        response = client.post("/api/config/apply", json={"env": env})
        assert response.status_code == 400, f"Expected 400 for {env}"


def test_config_apply_thresholds_read_only():
    """Thresholds cannot be changed via the API — schema.yaml is the source."""
    client = _client()
    response = client.post(
        "/api/config/apply",
        json={"thresholds": {"new_tag_min": 1}},
    )
    assert response.status_code == 400
    assert "read-only" in response.json()["detail"]


def test_config_apply_empty_body_is_noop():
    """An empty apply returns the current config unchanged."""
    client = _client()
    before = client.get("/api/config").json()
    response = client.post("/api/config/apply", json={})
    assert response.status_code == 200
    assert response.json() == before


def test_config_requires_auth_when_token_set():
    vault = create_test_vault()
    client = TestClient(create_app(Settings(vault_path=str(vault), token="secret")))

    assert client.get("/api/config").status_code == 401
    assert client.post("/api/config/apply", json={}).status_code == 401
    ok = client.get("/api/config", headers={"Authorization": "Bearer secret"})
    assert ok.status_code == 200

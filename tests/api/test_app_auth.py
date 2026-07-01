"""Tests for FastAPI app factory and auth middleware."""

import os
from unittest.mock import patch

from fastapi.testclient import TestClient

from agent_vault.api.app import create_app
from agent_vault.api.config import Settings


def test_health_endpoint_no_auth():
    """Test /api/health returns 200 when no token is configured."""
    settings = Settings(host="127.0.0.1", port=7778, vault_path=".", token="")
    app = create_app(settings)
    client = TestClient(app)

    response = client.get("/api/health")
    assert response.status_code == 200
    assert response.json() == {"ok": True}


def test_health_endpoint_with_valid_token():
    """Test /api/health returns 200 when valid token is provided."""
    settings = Settings(host="127.0.0.1", port=7778, vault_path=".", token="test-token")
    app = create_app(settings)
    client = TestClient(app)

    response = client.get("/api/health", headers={"Authorization": "Bearer test-token"})
    assert response.status_code == 200
    assert response.json() == {"ok": True}


def test_health_endpoint_with_missing_token():
    """Test /api/health returns 401 when token is configured but not provided."""
    settings = Settings(host="127.0.0.1", port=7778, vault_path=".", token="test-token")
    app = create_app(settings)
    client = TestClient(app)

    response = client.get("/api/health")
    assert response.status_code == 401
    assert "detail" in response.json()


def test_health_endpoint_with_wrong_token():
    """Test /api/health returns 401 when wrong token is provided."""
    settings = Settings(host="127.0.0.1", port=7778, vault_path=".", token="correct-token")
    app = create_app(settings)
    client = TestClient(app)

    response = client.get("/api/health", headers={"Authorization": "Bearer wrong-token"})
    assert response.status_code == 401
    assert "detail" in response.json()


def test_health_endpoint_with_malformed_auth():
    """Test /api/health returns 401 when Authorization header is malformed."""
    settings = Settings(host="127.0.0.1", port=7778, vault_path=".", token="test-token")
    app = create_app(settings)
    client = TestClient(app)

    # Missing "Bearer " prefix
    response = client.get("/api/health", headers={"Authorization": "test-token"})
    assert response.status_code == 401

    # Wrong scheme
    response = client.get("/api/health", headers={"Authorization": "Basic test-token"})
    assert response.status_code == 401


def test_settings_from_env():
    """Test Settings.from_env() reads from environment variables."""
    env_vars = {
        "AGENT_VAULT_HOST": "0.0.0.0",
        "AGENT_VAULT_PORT": "9000",
        "AGENT_VAULT_PATH": "/tmp/vault",
        "VAULT_TOKEN": "secret-token",
    }

    with patch.dict(os.environ, env_vars, clear=True):
        settings = Settings.from_env()
        assert settings.host == "0.0.0.0"
        assert settings.port == 9000
        assert settings.vault_path == "/tmp/vault"
        assert settings.token == "secret-token"


def test_settings_from_env_defaults():
    """Test Settings.from_env() uses defaults when env vars are unset."""
    with patch.dict(os.environ, {}, clear=True):
        settings = Settings.from_env()
        assert settings.host == "127.0.0.1"
        assert settings.port == 7778
        assert settings.vault_path == "."
        assert settings.token == ""


def test_openapi_metadata():
    """Test that OpenAPI schema includes correct metadata."""
    settings = Settings(host="127.0.0.1", port=7778, vault_path=".", token="")
    app = create_app(settings)

    assert app.title == "Agent Vault API"
    # FastAPI adds version automatically if not specified
    assert app.version  # Should have some version

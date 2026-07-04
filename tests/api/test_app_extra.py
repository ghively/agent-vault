"""Coverage for two previously-untested corners of the service:

  - Settings.from_env — env-var parsing / defaults / port coercion;
  - app.py's SPA static-serving block (favicon + index.html fallback) that only
    mounts when web/dist exists. Those routes are exercised when a build is
    present and skipped cleanly otherwise, so this passes with or without a
    built frontend.
"""
import tempfile

import pytest
from fastapi.testclient import TestClient

from agent_vault.api.app import create_app
from agent_vault.api.config import Settings


# ---------------------------------------------------------------------------
# Settings.from_env
# ---------------------------------------------------------------------------

def test_from_env_defaults(monkeypatch):
    for k in ("AGENT_VAULT_HOST", "AGENT_VAULT_PORT", "AGENT_VAULT_PATH", "VAULT_TOKEN"):
        monkeypatch.delenv(k, raising=False)
    s = Settings.from_env()
    assert (s.host, s.port, s.vault_path, s.token) == ("127.0.0.1", 7778, ".", "")


def test_from_env_reads_all_vars(monkeypatch):
    monkeypatch.setenv("AGENT_VAULT_HOST", "0.0.0.0")
    monkeypatch.setenv("AGENT_VAULT_PORT", "9001")
    monkeypatch.setenv("AGENT_VAULT_PATH", "/data/vault")
    monkeypatch.setenv("VAULT_TOKEN", "sekret")
    s = Settings.from_env()
    assert s.host == "0.0.0.0" and s.port == 9001
    assert s.vault_path == "/data/vault" and s.token == "sekret"


def test_from_env_bad_port_raises(monkeypatch):
    monkeypatch.setenv("AGENT_VAULT_PORT", "not-a-port")
    with pytest.raises(ValueError):
        Settings.from_env()


# ---------------------------------------------------------------------------
# SPA static-serving block (only when web/dist is present)
# ---------------------------------------------------------------------------

def _has_spa(app) -> bool:
    return any(getattr(r, "path", None) == "/{full_path:path}" for r in app.routes)


def test_spa_fallback_and_favicon_when_dist_present():
    app = create_app(Settings(vault_path=tempfile.mkdtemp(), token=""))
    if not _has_spa(app):
        pytest.skip("web/dist not built in this environment")
    client = TestClient(app)

    # /api routes are registered before the catch-all, so health is not shadowed
    assert client.get("/api/health").json() == {"ok": True}

    # any non-/api path falls back to the SPA index.html
    spa = client.get("/browse")
    assert spa.status_code == 200
    assert "text/html" in spa.headers.get("content-type", "")

    # favicon returns the file (200) or a clean 204 when absent
    assert client.get("/favicon.ico").status_code in (200, 204)


def test_health_open_without_dist_dependency():
    # health must work regardless of whether the SPA block mounted
    app = create_app(Settings(vault_path=tempfile.mkdtemp(), token=""))
    client = TestClient(app)
    assert client.get("/api/health").status_code == 200

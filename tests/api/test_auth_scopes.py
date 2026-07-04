"""Tests for per-agent identity, scoping, and the access audit (O5/O6)."""

import json
import tempfile
from pathlib import Path

from fastapi.testclient import TestClient

from agent_vault.api.app import create_app
from agent_vault.api.auth import (
    Identity, load_token_registry, required_scope,
)
from agent_vault.api.config import Settings


def _vault() -> Path:
    vault = Path(tempfile.mkdtemp())
    (vault / "entities" / "account").mkdir(parents=True)
    (vault / "discovery").mkdir(parents=True)
    (vault / "registry").mkdir(parents=True)
    (vault / "entities" / "account" / "bofa.md").write_text(
        "---\nslug: bofa\ntype: account\nsubtype: checking\ntitle: BofA\n"
        "status: compiled\ncredential_ref: env://BOFA\n---\n\n"
        "<!-- LINKS:BEGIN -->\n<!-- LINKS:END -->\n\nChecking.\n", encoding="utf-8")
    (vault / "_index.json").write_text(json.dumps({"entities": [
        {"slug": "bofa", "type": "account", "subtype": "checking", "title": "BofA",
         "status": "compiled", "path": "entities/account/bofa.md", "tags": []}]}),
        encoding="utf-8")
    return vault


# --- required_scope classification ------------------------------------------

def test_required_scope_classification():
    assert required_scope("GET", "/api/entities") == "read"
    assert required_scope("GET", "/api/search") == "read"
    assert required_scope("POST", "/api/jobs/run") == "write"
    assert required_scope("PATCH", "/api/entities/x") == "write"
    assert required_scope("POST", "/api/creds/bofa/resolve") == "resolve"
    assert required_scope("GET", "/api/health") is None
    assert required_scope("GET", "/docs") is None


# --- token registry loading -------------------------------------------------

def test_legacy_token_is_admin():
    reg = load_token_registry(Settings(vault_path=".", token="legacy"))
    assert reg["legacy"].actor == "vault-token"
    assert reg["legacy"].allows("write") and reg["legacy"].allows("resolve")


def test_vault_tokens_env_registry(monkeypatch):
    monkeypatch.setenv("VAULT_TOKENS", json.dumps({
        "rtok": {"actor": "reader", "scopes": ["read"]},
        "wtok": {"actor": "writer", "scopes": ["read", "write"]},
    }))
    reg = load_token_registry(Settings(vault_path=".", token=""))
    assert reg["rtok"] == Identity("reader", frozenset({"read"}))
    assert reg["wtok"].allows("write")
    assert not reg["rtok"].allows("write")


def test_tokens_yaml_registry(tmp_path):
    (tmp_path / "registry").mkdir()
    (tmp_path / "registry" / "tokens.yaml").write_text(
        "tokens:\n  ytok:\n    actor: yaml-agent\n    scopes: [read, resolve]\n",
        encoding="utf-8")
    reg = load_token_registry(Settings(vault_path=str(tmp_path), token=""))
    assert reg["ytok"].actor == "yaml-agent"
    assert reg["ytok"].allows("resolve")
    assert not reg["ytok"].allows("write")


# --- end-to-end scope enforcement -------------------------------------------

def _client(vault: Path, monkeypatch, tokens: dict) -> TestClient:
    monkeypatch.setenv("VAULT_TOKENS", json.dumps(tokens))
    return TestClient(create_app(Settings(vault_path=str(vault), token="")))


def test_read_token_can_read_but_not_write(monkeypatch):
    vault = _vault()
    client = _client(vault, monkeypatch, {"rtok": {"actor": "reader", "scopes": ["read"]}})
    h = {"Authorization": "Bearer rtok"}
    assert client.get("/api/entities", headers=h).status_code == 200
    # a write route is forbidden for a read-only token
    r = client.post("/api/jobs/run", headers=h, json={"op": "lint", "args": ["--json"]})
    assert r.status_code == 403
    assert "scope" in r.json()["detail"]


def test_write_token_can_write(monkeypatch):
    vault = _vault()
    client = _client(vault, monkeypatch,
                     {"wtok": {"actor": "writer", "scopes": ["read", "write"]}})
    h = {"Authorization": "Bearer wtok"}
    # 200 (job accepted) — the point is it's NOT 403
    assert client.post("/api/jobs/run", headers=h,
                       json={"op": "lint", "args": ["--json"]}).status_code == 200


def test_resolve_scope_gates_credential_resolution(monkeypatch):
    vault = _vault()
    client = _client(vault, monkeypatch, {
        "rtok": {"actor": "reader", "scopes": ["read"]},
        "stok": {"actor": "secret-agent", "scopes": ["read", "resolve"]},
    })
    # read-only token cannot resolve
    assert client.post("/api/creds/bofa/resolve",
                       headers={"Authorization": "Bearer rtok"}).status_code == 403
    # resolve-scoped token passes the scope gate (resolution itself may 200/502
    # depending on backend; the point is it's not a 403 scope denial)
    r = client.post("/api/creds/bofa/resolve", headers={"Authorization": "Bearer stok"})
    assert r.status_code != 403


def test_unknown_token_is_401(monkeypatch):
    vault = _vault()
    client = _client(vault, monkeypatch, {"rtok": {"actor": "r", "scopes": ["read"]}})
    assert client.get("/api/entities",
                      headers={"Authorization": "Bearer nope"}).status_code == 401


# --- audit trail ------------------------------------------------------------

def test_audit_records_writes_with_actor(monkeypatch):
    vault = _vault()
    client = _client(vault, monkeypatch,
                     {"wtok": {"actor": "writer", "scopes": ["read", "write"]}})
    client.post("/api/jobs/run", headers={"Authorization": "Bearer wtok"},
                json={"op": "lint", "args": ["--json"]})
    access = vault / "discovery" / "_access.jsonl"
    assert access.exists()
    recs = [json.loads(x) for x in access.read_text().splitlines() if x.strip()]
    last = recs[-1]
    assert last["actor"] == "writer"
    assert last["scope"] == "write"
    assert last["path"] == "/api/jobs/run"
    assert "status" in last


def test_audit_records_resolve_target_never_secret(monkeypatch):
    vault = _vault()
    monkeypatch.setenv("BOFA", "hunter2")
    client = _client(vault, monkeypatch,
                     {"stok": {"actor": "sec", "scopes": ["read", "resolve"]}})
    client.post("/api/creds/bofa/resolve", headers={"Authorization": "Bearer stok"})
    recs = [json.loads(x) for x in
            (vault / "discovery" / "_access.jsonl").read_text().splitlines() if x.strip()]
    resolve_recs = [r for r in recs if r["scope"] == "resolve"]
    assert resolve_recs
    rec = resolve_recs[-1]
    assert rec["action"] == "resolve"
    assert rec["target"] == "bofa"
    # the secret must never appear anywhere in the audit line
    assert "hunter2" not in json.dumps(rec)


def test_reads_are_not_audited(monkeypatch):
    vault = _vault()
    client = _client(vault, monkeypatch, {"rtok": {"actor": "r", "scopes": ["read"]}})
    client.get("/api/entities", headers={"Authorization": "Bearer rtok"})
    access = vault / "discovery" / "_access.jsonl"
    # a pure read produces no audit line
    if access.exists():
        recs = [json.loads(x) for x in access.read_text().splitlines() if x.strip()]
        assert all(r["scope"] != "read" for r in recs)

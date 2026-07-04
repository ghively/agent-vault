"""Tests for GET /api/settings (read-only configuration overview).

This was the only /api endpoint with no test. Covers the happy-path response
shape plus the two defensive fallbacks in settings.py: a corrupt _index.json
counts as 0 entities (not a 500), and an unparseable resolvers.yaml yields
(default=None, backends=[]).
"""

import json
import tempfile
from pathlib import Path

from fastapi.testclient import TestClient

from agent_vault.api.app import create_app
from agent_vault.api.config import Settings

RESOLVERS_YAML = """\
version: 1
default_resolver: age
resolvers:
  age:
    module: resolvers.age_file
    description: |
      age-encrypted files on the NAS.
      second line should be dropped
  env:
    module: resolvers.env
    description: Environment variables.
"""


def _vault_with_config() -> Path:
    vault = Path(tempfile.mkdtemp())
    (vault / "registry").mkdir(parents=True)
    (vault / "cadences").mkdir(parents=True)
    (vault / "_index.json").write_text(
        json.dumps({"count": 2, "entities": [{"slug": "a"}, {"slug": "b"}]}),
        encoding="utf-8",
    )
    (vault / "registry" / "resolvers.yaml").write_text(RESOLVERS_YAML, encoding="utf-8")
    (vault / "cadences" / "daily.sh").write_text("#!/bin/sh\n", encoding="utf-8")
    (vault / "cadences" / "weekly.sh").write_text("#!/bin/sh\n", encoding="utf-8")
    return vault


def test_settings_happy_path():
    vault = _vault_with_config()
    app = create_app(Settings(vault_path=str(vault), token="", host="0.0.0.0", port=9999))
    client = TestClient(app)

    r = client.get("/api/settings")
    assert r.status_code == 200
    data = r.json()

    assert data["service"]["vault_path"] == str(vault)
    assert data["service"]["host"] == "0.0.0.0"
    assert data["service"]["port"] == 9999
    assert data["service"]["auth_enabled"] is False
    assert isinstance(data["service"]["version"], str)

    assert data["vault"] == {"entity_count": 2, "index_built": True}

    # compiler contract version comes from agent_vault.compiler
    assert data["compiler"]["prompt_contract_version"] == "2.0"

    # resolvers: default + backends, descriptions collapsed to first line
    assert data["resolvers"]["default"] == "age"
    schemes = {b["scheme"]: b for b in data["resolvers"]["backends"]}
    assert set(schemes) == {"age", "env"}
    assert schemes["age"]["module"] == "resolvers.age_file"
    assert schemes["age"]["description"] == "age-encrypted files on the NAS."  # only first line

    assert data["cadences"] == ["daily.sh", "weekly.sh"]


def test_settings_auth_enabled_flag_and_gate():
    vault = _vault_with_config()
    app = create_app(Settings(vault_path=str(vault), token="secret"))
    client = TestClient(app)

    assert client.get("/api/settings").status_code == 401
    ok = client.get("/api/settings", headers={"Authorization": "Bearer secret"})
    assert ok.status_code == 200
    assert ok.json()["service"]["auth_enabled"] is True


def test_settings_corrupt_index_counts_zero():
    vault = _vault_with_config()
    (vault / "_index.json").write_text("{not valid json", encoding="utf-8")
    app = create_app(Settings(vault_path=str(vault), token=""))
    client = TestClient(app)

    data = client.get("/api/settings").json()
    # index file exists (built) but is unreadable -> count degrades to 0, no 500
    assert data["vault"] == {"entity_count": 0, "index_built": True}


def test_settings_bad_resolvers_yaml_degrades():
    vault = _vault_with_config()
    (vault / "registry" / "resolvers.yaml").write_text("::: not: [valid", encoding="utf-8")
    app = create_app(Settings(vault_path=str(vault), token=""))
    client = TestClient(app)

    data = client.get("/api/settings").json()
    assert data["resolvers"] == {"default": None, "backends": []}


def test_settings_empty_vault():
    vault = Path(tempfile.mkdtemp())
    app = create_app(Settings(vault_path=str(vault), token=""))
    client = TestClient(app)

    data = client.get("/api/settings").json()
    assert data["vault"] == {"entity_count": 0, "index_built": False}
    assert data["resolvers"] == {"default": None, "backends": []}
    assert data["cadences"] == []

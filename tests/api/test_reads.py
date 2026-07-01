"""Tests for read endpoints (entities, review queues, creds)."""

import json
import os
import tempfile
from pathlib import Path

from fastapi.testclient import TestClient

from agent_vault.api.app import create_app
from agent_vault.api.config import Settings


def create_test_vault() -> Path:
    """Create a minimal test vault with sample entities."""
    vault_dir = tempfile.mkdtemp()
    vault = Path(vault_dir)

    # Create entities directory structure
    (vault / "entities" / "account").mkdir(parents=True)
    (vault / "entities" / "asset").mkdir(parents=True)
    (vault / "entities" / "document").mkdir(parents=True)
    (vault / "discovery").mkdir(parents=True)
    (vault / "registry").mkdir(parents=True)

    # Entity 1: compiled account with credential
    entity1_md = """---
slug: bofa-checking
type: account
subtype: checking
title: Bank of America — Primary Checking
status: compiled
confidence: 0.97
created: 2026-05-21
sources:
  - raw/statements/bofa-checking-2026-01.pdf
sources_hash: seed000002
tags: [banking, primary]
last4: "4417"
credential_ref: age://banking/bofa-login
---

<!-- LINKS:BEGIN -->
- Held at: [[financial-institution/bank-of-america]]
<!-- LINKS:END -->

Primary household checking account at Bank of America.
"""
    (vault / "entities" / "account" / "bofa-checking.md").write_text(entity1_md)

    # Entity 2: needs-review gated entity
    entity2_md = """---
slug: new-gateway
type: service
subtype: api-gateway
title: New Cloud Gateway
status: needs-review
confidence: 0.3
inferred: true
tags: [network]
---
New gateway service detected, needs human review.
"""
    (vault / "entities" / "service").mkdir(parents=True, exist_ok=True)
    (vault / "entities" / "service" / "new-gateway.md").write_text(entity2_md)

    # Entity 3: compiled asset
    entity3_md = """---
slug: honda-crv
type: asset
subtype: vehicle
title: 2019 Honda CR-V
status: compiled
confidence: 0.95
created: 2026-01-15
tags: [vehicle]
model: CR-V
serial: ABC123
---

<!-- LINKS:BEGIN -->
- Insurance: [[document/geico-policy]]
<!-- LINKS:END -->

Family vehicle.
"""
    (vault / "entities" / "asset" / "honda-crv.md").write_text(entity3_md)

    # Entity 4: another account without credential
    entity4_md = """---
slug: savings-account
type: account
subtype: savings
title: Emergency Savings
status: compiled
confidence: 0.98
tags: [banking]
---

Emergency fund savings account.
"""
    (vault / "entities" / "account" / "savings-account.md").write_text(entity4_md)

    # Build index.json
    index_data = {
        "entities": [
            {
                "slug": "bofa-checking",
                "type": "account",
                "subtype": "checking",
                "title": "Bank of America — Primary Checking",
                "status": "compiled",
                "confidence": 0.97,
                "path": "entities/account/bofa-checking.md",
                "tags": ["banking", "primary"],
                "has_credential": True,
            },
            {
                "slug": "new-gateway",
                "type": "service",
                "subtype": "api-gateway",
                "title": "New Cloud Gateway",
                "status": "needs-review",
                "confidence": 0.3,
                "path": "entities/service/new-gateway.md",
                "tags": ["network"],
                "inferred": True,
                "has_credential": False,
            },
            {
                "slug": "honda-crv",
                "type": "asset",
                "subtype": "vehicle",
                "title": "2019 Honda CR-V",
                "status": "compiled",
                "confidence": 0.95,
                "path": "entities/asset/honda-crv.md",
                "tags": ["vehicle"],
                "has_credential": False,
            },
            {
                "slug": "savings-account",
                "type": "account",
                "subtype": "savings",
                "title": "Emergency Savings",
                "status": "compiled",
                "confidence": 0.98,
                "path": "entities/account/savings-account.md",
                "tags": ["banking"],
                "has_credential": False,
            },
        ]
    }
    (vault / "_index.json").write_text(json.dumps(index_data))

    # Create a sample promoted.jsonl with one pending proposal
    promoted_jsonl = """{"identity": ["type", "service", "cdn"], "kind": "new_type", "action": "queue_for_review", "sightings": 5, "reason": "New service type detected"}
"""
    (vault / "discovery" / "promoted.jsonl").write_text(promoted_jsonl)

    # Create schema.yaml with gated types
    schema_yaml = """
types:
  service:
    human_gated: true
  account:
    human_gated: false
"""
    (vault / "registry" / "schema.yaml").write_text(schema_yaml)

    return vault


def test_list_entities_all():
    """Test GET /api/entities returns all entities."""
    vault = create_test_vault()
    settings = Settings(vault_path=str(vault), token="")
    app = create_app(settings)
    client = TestClient(app)

    response = client.get("/api/entities")
    assert response.status_code == 200

    data = response.json()
    assert "rows" in data
    assert "total" in data
    assert data["total"] == 4
    assert len(data["rows"]) == 4

    # Check first entity structure
    row = data["rows"][0]
    assert "slug" in row
    assert "type" in row
    assert "subtype" in row
    assert "status" in row
    assert "confidence" in row
    assert "tags" in row
    assert "ts" in row  # type/subtype combo


def test_list_entities_filter_by_type():
    """Test GET /api/entities?type=account filters by type."""
    vault = create_test_vault()
    settings = Settings(vault_path=str(vault), token="")
    app = create_app(settings)
    client = TestClient(app)

    response = client.get("/api/entities?type=account")
    assert response.status_code == 200

    data = response.json()
    assert len(data["rows"]) == 2
    assert all(e["type"] == "account" for e in data["rows"])
    assert data["total"] == 4  # Total is unaffected by filter


def test_list_entities_search():
    """Test GET /api/entities?q=search filters by query."""
    vault = create_test_vault()
    settings = Settings(vault_path=str(vault), token="")
    app = create_app(settings)
    client = TestClient(app)

    response = client.get("/api/entities?q=honda")
    assert response.status_code == 200

    data = response.json()
    assert len(data["rows"]) == 1
    assert data["rows"][0]["slug"] == "honda-crv"


def test_get_entity_detail():
    """Test GET /api/entities/{slug} returns full entity record."""
    vault = create_test_vault()
    settings = Settings(vault_path=str(vault), token="")
    app = create_app(settings)
    client = TestClient(app)

    response = client.get("/api/entities/bofa-checking")
    assert response.status_code == 200

    data = response.json()
    assert data["slug"] == "bofa-checking"
    assert data["title"] == "Bank of America — Primary Checking"
    assert data["type"] == "account"
    assert data["status"] == "compiled"
    assert "prose" in data
    assert "sources" in data
    assert "facts" in data
    assert "links" in data

    # Check prose contains main content
    assert "Bank of America" in data["prose"]

    # Check facts include last4
    assert any(f["k"] == "last4" and f["v"] == "4417" for f in data["facts"])

    # Check links are resolved
    assert len(data["links"]) == 1
    assert data["links"][0]["label"] == "Held at"
    assert data["links"][0]["ref"] == "financial-institution/bank-of-america"


def test_get_entity_not_found():
    """Test GET /api/entities/{slug} returns 404 for unknown entity."""
    vault = create_test_vault()
    settings = Settings(vault_path=str(vault), token="")
    app = create_app(settings)
    client = TestClient(app)

    response = client.get("/api/entities/nonexistent")
    assert response.status_code == 404


def test_list_review_proposals():
    """Test GET /api/review/proposals returns pending proposals."""
    vault = create_test_vault()
    settings = Settings(vault_path=str(vault), token="")
    app = create_app(settings)
    client = TestClient(app)

    response = client.get("/api/review/proposals")
    assert response.status_code == 200

    data = response.json()
    assert "items" in data
    assert len(data["items"]) == 1

    proposal = data["items"][0]
    assert "id" in proposal
    assert "kind" in proposal
    assert "concept" in proposal
    assert "sightings" in proposal
    assert proposal["kind"] == "new_type"
    assert proposal["sightings"] == 5


def test_list_review_entities():
    """Test GET /api/review/entities returns needs-review entities."""
    vault = create_test_vault()
    settings = Settings(vault_path=str(vault), token="")
    app = create_app(settings)
    client = TestClient(app)

    response = client.get("/api/review/entities")
    assert response.status_code == 200

    data = response.json()
    assert "items" in data
    assert len(data["items"]) == 1

    entity = data["items"][0]
    assert entity["ref"] == "service/new-gateway"
    assert entity["title"] == "New Cloud Gateway"
    assert entity["gated"] is True  # service type is human_gated in schema
    assert entity["inferred"] is True


def test_list_creds():
    """Test GET /api/creds returns credential refs (not plaintext)."""
    vault = create_test_vault()
    settings = Settings(vault_path=str(vault), token="")
    app = create_app(settings)
    client = TestClient(app)

    response = client.get("/api/creds")
    assert response.status_code == 200

    data = response.json()
    assert "items" in data
    assert len(data["items"]) == 1

    cred = data["items"][0]
    assert cred["slug"] == "bofa-checking"
    assert cred["title"] == "Bank of America — Primary Checking"
    assert cred["ref"] == "age://banking/bofa-login"
    assert cred["backend"] == "age"

    # Ensure no plaintext secrets
    assert "password" not in str(data).lower()
    assert "secret" not in str(data).lower()


def test_list_creds_empty_vault():
    """Test GET /api/creds returns empty list when no creds."""
    vault = create_test_vault()
    # Remove credential from entity
    entity_md = (vault / "entities" / "account" / "bofa-checking.md").read_text()
    entity_md_no_cred = "\n".join(
        line for line in entity_md.split("\n") if "credential_ref" not in line
    )
    (vault / "entities" / "account" / "bofa-checking.md").write_text(entity_md_no_cred)

    # Update index
    index_data = json.loads((vault / "_index.json").read_text())
    index_data["entities"][0]["has_credential"] = False
    (vault / "_index.json").write_text(json.dumps(index_data))

    settings = Settings(vault_path=str(vault), token="")
    app = create_app(settings)
    client = TestClient(app)

    response = client.get("/api/creds")
    assert response.status_code == 200

    data = response.json()
    assert data["items"] == []


def test_entities_response_shape_matches_synapsenas():
    """Test that response shapes match SynapseNAS patterns."""
    vault = create_test_vault()
    settings = Settings(vault_path=str(vault), token="")
    app = create_app(settings)
    client = TestClient(app)

    # /api/entities shape
    response = client.get("/api/entities")
    data = response.json()
    assert set(data.keys()) == {"rows", "total"}
    assert set(data["rows"][0].keys()) >= {
        "slug",
        "ts",
        "type",
        "subtype",
        "status",
        "confidence",
        "tags",
    }

    # /api/entities/{slug} shape
    response = client.get("/api/entities/bofa-checking")
    data = response.json()
    assert set(data.keys()) >= {
        "slug",
        "title",
        "type",
        "subtype",
        "status",
        "confidence",
        "hash",
        "prose",
        "sources",
        "facts",
        "links",
    }

    # /api/review/proposals shape
    response = client.get("/api/review/proposals")
    data = response.json()
    assert set(data.keys()) == {"items"}
    assert set(data["items"][0].keys()) >= {"id", "kind", "concept", "sightings", "reason"}

    # /api/review/entities shape
    response = client.get("/api/review/entities")
    data = response.json()
    assert set(data.keys()) == {"items"}
    assert set(data["items"][0].keys()) >= {"ref", "title", "confidence", "gated", "inferred"}

    # /api/creds shape
    response = client.get("/api/creds")
    data = response.json()
    assert set(data.keys()) == {"items"}
    assert set(data["items"][0].keys()) == {"slug", "title", "ref", "backend"}

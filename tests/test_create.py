"""Tests for vault_create — structured entity creation tool (Phase 2)."""
import json
from pathlib import Path

import pytest

from agent_vault import mcp_tools


def _make_vault_with_schema(tmp_path: Path) -> Path:
    """Create a minimal vault with schema.yaml for type validation."""
    vault = tmp_path / "vault"
    (vault / "entities" / "asset").mkdir(parents=True)
    (vault / "entities" / "account").mkdir(parents=True)
    (vault / "discovery").mkdir(parents=True)
    (vault / "registry").mkdir(parents=True)

    # Minimal resolver registry so the env backend is wired.
    (vault / "registry" / "resolvers.yaml").write_text(
        "version: 1\ndefault_resolver: env\nresolvers:\n"
        "  env:\n    module: agent_vault.resolvers.env\n",
        encoding="utf-8",
    )

    # Minimal schema.yaml with a few type/subtype pairs.
    (vault / "registry" / "schema.yaml").write_text(
        """types:
  account:
    subtypes: [checking, savings, credit-card]
  asset:
    subtypes: [hvac, vehicle, appliance]
""",
        encoding="utf-8",
    )

    # Build the index.
    from agent_vault import build_index
    import sys
    argv = sys.argv
    sys.argv = ["build_index", str(vault)]
    try:
        build_index.main()
    finally:
        sys.argv = argv
    return vault


def test_create_simple_entity(tmp_path):
    """Test successful creation of a minimal entity."""
    vault = _make_vault_with_schema(tmp_path)
    result = mcp_tools.create(
        str(vault),
        slug="test-asset",
        type_="asset",
        subtype="hvac",
        data={"title": "Test HVAC", "status": "stub"},
    )
    assert result["ok"] is True
    assert result["slug"] == "test-asset"
    assert "entities/asset/test-asset.md" in result["path"]
    # Verify file exists and has required fields.
    entity_path = vault / result["path"]
    assert entity_path.exists()
    content = entity_path.read_text(encoding="utf-8")
    assert "slug: test-asset" in content
    assert "type: asset" in content
    assert "subtype: hvac" in content
    assert "title: Test HVAC" in content
    assert "status: stub" in content
    # Verify index was rebuilt.
    index_path = vault / "_index.json"
    assert index_path.exists()
    index = json.loads(index_path.read_text(encoding="utf-8"))
    assert any(e["slug"] == "test-asset" for e in index["entities"])


def test_create_with_defaults(tmp_path):
    """Test that defaults are populated (title from slug, status=stub)."""
    vault = _make_vault_with_schema(tmp_path)
    result = mcp_tools.create(
        str(vault),
        slug="my-account",
        type_="account",
        subtype="checking",
        data=None,
    )
    assert result["ok"] is True
    entity_path = vault / result["path"]
    content = entity_path.read_text(encoding="utf-8")
    assert "title: My Account" in content  # derived from slug
    assert "status: stub" in content
    assert "confidence: 1.0" in content  # default


def test_create_with_prose(tmp_path):
    """Test creation with prose body."""
    vault = _make_vault_with_schema(tmp_path)
    result = mcp_tools.create(
        str(vault),
        slug="test-car",
        type_="asset",
        subtype="vehicle",
        data={"title": "Test Car", "make": "Toyota", "model": "Camry"},
        prose="This is a test car entity.",
    )
    assert result["ok"] is True
    entity_path = vault / result["path"]
    content = entity_path.read_text(encoding="utf-8")
    assert "This is a test car entity." in content
    assert "make: Toyota" in content
    assert "model: Camry" in content


def test_create_with_related_links(tmp_path):
    """Test that LINKS block is generated from related entries."""
    vault = _make_vault_with_schema(tmp_path)
    # Create a target entity to relate to.
    mcp_tools.create(
        str(vault),
        slug="target-account",
        type_="account",
        subtype="savings",
        data={"title": "Target Account"},
    )
    result = mcp_tools.create(
        str(vault),
        slug="related-asset",
        type_="asset",
        subtype="appliance",
        data={"title": "Related Asset", "related": ["target-account"]},
    )
    assert result["ok"] is True
    entity_path = vault / result["path"]
    content = entity_path.read_text(encoding="utf-8")
    assert "<!-- LINKS:BEGIN -->" in content
    assert "<!-- LINKS:END -->" in content
    assert "[[target-account]]" in content


def test_create_rejects_invalid_slug(tmp_path):
    """Test that invalid slug format is rejected."""
    vault = _make_vault_with_schema(tmp_path)
    result = mcp_tools.create(
        str(vault),
        slug="Bad Slug!",
        type_="asset",
        subtype="hvac",
    )
    assert "error" in result
    assert "invalid slug" in result["error"].lower()


def test_create_rejects_unknown_type(tmp_path):
    """Test that unknown type is rejected."""
    vault = _make_vault_with_schema(tmp_path)
    result = mcp_tools.create(
        str(vault),
        slug="test-entity",
        type_="unknown-type",
        subtype="something",
    )
    assert "error" in result
    assert "unknown type" in result["error"].lower()


def test_create_rejects_unknown_subtype(tmp_path):
    """Test that unknown subtype is rejected."""
    vault = _make_vault_with_schema(tmp_path)
    result = mcp_tools.create(
        str(vault),
        slug="test-entity",
        type_="account",
        subtype="unknown-subtype",
    )
    assert "error" in result
    assert "unknown subtype" in result["error"].lower()


def test_create_refuses_overwrite(tmp_path):
    """Test that creating an entity that already exists fails."""
    vault = _make_vault_with_schema(tmp_path)
    mcp_tools.create(
        str(vault),
        slug="existing-entity",
        type_="asset",
        subtype="hvac",
        data={"title": "First"},
    )
    result = mcp_tools.create(
        str(vault),
        slug="existing-entity",
        type_="asset",
        subtype="hvac",
        data={"title": "Second"},
    )
    assert "error" in result
    assert "already exists" in result["error"].lower()


def test_create_with_optional_fields(tmp_path):
    """Test that optional fields like tags, location, etc. are preserved."""
    vault = _make_vault_with_schema(tmp_path)
    result = mcp_tools.create(
        str(vault),
        slug="detailed-entity",
        type_="account",
        subtype="credit-card",
        data={
            "title": "My Card",
            "tags": ["personal", "visa"],
            "location": "Wallet",
            "last4": "1234",
            "credential_ref": "env://TEST_SECRET",
        },
    )
    assert result["ok"] is True
    entity_path = vault / result["path"]
    content = entity_path.read_text(encoding="utf-8")
    assert "tags:" in content
    assert "- personal" in content
    assert "location: Wallet" in content
    assert "last4:" in content and "1234" in content
    assert "credential_ref: env://TEST_SECRET" in content


def test_vault_get_finds_created_entity(tmp_path):
    """Test that vault_get can retrieve an entity created via vault_create."""
    vault = _make_vault_with_schema(tmp_path)
    mcp_tools.create(
        str(vault),
        slug="get-test",
        type_="asset",
        subtype="vehicle",
        data={"title": "Get Test Vehicle", "make": "Honda", "model": "Civic"},
        prose="A test car for vault_get.",
    )
    # Rebuild index (create() should have done this, but be explicit).
    from agent_vault import build_index
    import sys
    argv = sys.argv
    sys.argv = ["build_index", str(vault)]
    try:
        build_index.main()
    finally:
        sys.argv = argv

    out = mcp_tools.get_entity(str(vault), "get-test")
    assert out["slug"] == "get-test"
    assert out["title"] == "Get Test Vehicle"
    assert "prose" in out
    assert "test car for vault_get" in out["prose"].lower()
    # 'model' is a known fact field; 'make' is stored in frontmatter only.
    fact_keys = [f["k"] for f in out.get("facts", [])]
    assert "model" in fact_keys
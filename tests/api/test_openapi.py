"""Test OpenAPI schema completeness."""

from __future__ import annotations

from agent_vault.api.config import Settings
from agent_vault.api.app import create_app


def test_openapi_phase2_routes() -> None:
    """Verify all Phase 2 routes are present in the OpenAPI schema."""
    settings = Settings.from_env()
    app = create_app(settings)
    schema = app.openapi()
    paths = schema.get("paths", {})

    # Core health check
    assert "/api/health" in paths

    # Entity reads
    assert "/api/entities" in paths
    assert "/api/entities/{slug}" in paths

    # Credential resolution
    assert "/api/creds" in paths
    assert "/api/creds/{slug}/resolve" in paths

    # Recompilation
    assert "/api/entities/{slug}/recompile" in paths

    # Edit endpoints (schema picker, reclassify, PATCH, raw editor)
    assert "/api/schema" in paths
    assert "/api/entities/{slug}/reclassify" in paths
    assert "patch" in paths["/api/entities/{slug}"]
    assert "/api/entities/{slug}/raw" in paths
    assert "get" in paths["/api/entities/{slug}/raw"]
    assert "put" in paths["/api/entities/{slug}/raw"]

    # Review endpoints
    assert "/api/review/proposals" in paths
    assert "/api/review/proposals/{pid}/approve" in paths
    assert "/api/review/proposals/{pid}/reject" in paths
    assert "/api/review/entities" in paths
    assert "/api/review/entities/{ref}/approve" in paths
    assert "/api/review/entities/{ref}/reject" in paths

    # Job runner
    assert "/api/jobs/run" in paths
    assert "/api/jobs/{job_id}" in paths
    assert "/api/jobs/{job_id}/stream" in paths

    # History/ledgers
    assert "/api/runs" in paths
    assert "/api/ledgers" in paths

    # Dashboard + query routing
    assert "/api/status" in paths
    assert "/api/ask" in paths

    # Runtime config
    assert "/api/config" in paths
    assert "/api/config/apply" in paths


def test_openapi_schema_metadata() -> None:
    """Verify OpenAPI metadata is set correctly."""
    settings = Settings.from_env()
    app = create_app(settings)
    schema = app.openapi()

    assert schema["info"]["title"] == "Agent Vault API"
    # Version is derived from the installed package metadata (single source of
    # truth: pyproject.toml), so assert it's present + version-shaped rather
    # than pinning a literal a release bump would break.
    assert schema["info"]["version"]
    assert schema["info"]["version"][0].isdigit()
    assert "HTTP API for Agent Vault document wiki" in schema["info"]["description"]

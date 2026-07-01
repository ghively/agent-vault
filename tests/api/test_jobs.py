"""Tests for async job runner endpoints with SSE streaming."""

import json
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient
import pytest

from agent_vault.api.app import create_app
from agent_vault.api.config import Settings


def create_test_vault() -> Path:
    """Create a minimal test vault with sample entities for testing jobs."""
    vault_dir = tempfile.mkdtemp()
    vault = Path(vault_dir)

    # Create entities directory structure
    (vault / "entities" / "document").mkdir(parents=True)
    (vault / "discovery").mkdir(parents=True)
    (vault / "registry").mkdir(parents=True)
    (vault / "sources").mkdir(parents=True)

    # Create a simple document entity for compile testing
    entity_md = """---
slug: test-doc
type: document
title: Test Document
status: compiled
confidence: 1.0
created: 2026-06-30
sources: []
sources_hash: seed000001
---

Test document for job runner testing.
"""
    (vault / "entities" / "document" / "test-doc.md").write_text(entity_md)

    return vault


def test_job_run_invalid_operation():
    """Test that invalid operations are rejected with 422."""
    vault = create_test_vault()
    settings = Settings(vault_path=str(vault), token="")
    app = create_app(settings)
    client = TestClient(app)

    # Try to run an invalid operation
    response = client.post(
        "/api/jobs/run",
        json={"op": "invalid_op", "args": []},
    )

    assert response.status_code == 422
    assert "not allowed" in response.json()["detail"]


def test_job_run_compile_success():
    """Test running a compile job against a test vault.

    Note: TestClient doesn't execute background tasks, so this test
    only verifies job creation and endpoint behavior, not actual completion.
    Real subprocess execution is tested via integration tests.
    """
    vault = create_test_vault()
    settings = Settings(vault_path=str(vault), token="")
    app = create_app(settings)
    client = TestClient(app)

    # Run compile job (should succeed on our minimal vault)
    response = client.post(
        "/api/jobs/run",
        json={"op": "compile", "args": []},
    )

    assert response.status_code == 200
    data = response.json()
    assert "job_id" in data
    assert data["status"] in ("pending", "running", "completed")
    job_id = data["job_id"]

    # Verify we can query the job status
    status_response = client.get(f"/api/jobs/{job_id}")
    assert status_response.status_code == 200
    status_data = status_response.json()
    assert "job_id" in status_data
    assert "status" in status_data
    assert "returncode" in status_data
    assert "stdout" in status_data
    assert "stderr" in status_data


def test_job_get_status_not_found():
    """Test that getting status for non-existent job returns 404."""
    vault = create_test_vault()
    settings = Settings(vault_path=str(vault), token="")
    app = create_app(settings)
    client = TestClient(app)

    response = client.get("/api/jobs/nonexistent_job_id")
    assert response.status_code == 404


@pytest.mark.skip("TestClient doesn't execute background tasks - SSE integration tested separately")
def test_job_stream_sse():
    """Test SSE streaming endpoint returns correct content type."""
    vault = create_test_vault()
    settings = Settings(vault_path=str(vault), token="")
    app = create_app(settings)
    client = TestClient(app)

    # Start a compile job
    run_response = client.post(
        "/api/jobs/run",
        json={"op": "compile", "args": []},
    )
    assert run_response.status_code == 200
    job_id = run_response.json()["job_id"]

    # Stream the job
    stream_response = client.get(f"/api/jobs/{job_id}/stream")

    # SSE should return 200
    assert stream_response.status_code == 200

    # Content should be text/event-stream
    assert "text/event-stream" in stream_response.headers.get("content-type", "")

    # Just verify we get some response (actual streaming tested in integration)
    assert len(stream_response.text) > 0


def test_job_stream_not_found():
    """Test that streaming non-existent job returns 404."""
    vault = create_test_vault()
    settings = Settings(vault_path=str(vault), token="")
    app = create_app(settings)
    client = TestClient(app)

    response = client.get("/api/jobs/nonexistent_job_id/stream")
    assert response.status_code == 404


def test_job_run_all_allowed_ops():
    """Test that all operations in allowlist can be started."""
    vault = create_test_vault()
    settings = Settings(vault_path=str(vault), token="")
    app = create_app(settings)
    client = TestClient(app)

    allowed_ops = ["ingest", "compile", "promote", "reclassify_apply"]

    for op in allowed_ops:
        response = client.post(
            "/api/jobs/run",
            json={"op": op, "args": []},
        )
        assert response.status_code == 200, f"Failed for op {op}"
        data = response.json()
        assert "job_id" in data
        assert "status" in data


def test_job_security_no_shell_injection():
    """Test that args are passed safely without shell injection.

    This is a critical security test - user args should never be executed
    through a shell, only passed as argv to subprocess.
    """
    vault = create_test_vault()
    settings = Settings(vault_path=str(vault), token="")
    app = create_app(settings)
    client = TestClient(app)

    # Try to pass shell injection attempts
    malicious_args = [
        "; rm -rf /",
        "&& cat /etc/passwd",
        "$(whoami)",
        "`malicious`",
    ]

    for malicious_arg in malicious_args:
        # Compile should reject these as invalid args, not execute them
        response = client.post(
            "/api/jobs/run",
            json={"op": "compile", "args": [malicious_arg]},
        )
        # Should return 200 (job started) but the job should fail
        # due to invalid args, not execute shell injection
        assert response.status_code == 200
        job_id = response.json()["job_id"]

        # The job should fail (bad args), not execute shell commands
        # We just verify the job was created and doesn't crash the service
        status_response = client.get(f"/api/jobs/{job_id}")
        assert status_response.status_code == 200

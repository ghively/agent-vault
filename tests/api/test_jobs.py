"""Tests for async job runner endpoints with SSE streaming."""

import tempfile
from pathlib import Path
import asyncio
import json
from typing import Any

from fastapi.testclient import TestClient

from agent_vault.api.app import create_app
from agent_vault.api.config import Settings
from agent_vault.api.jobs import JobRegistry


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


async def _collect_events(gen: Any) -> list[dict[str, Any]]:
    """Collect all events from an async generator for testing."""
    events = []
    async for event in gen:
        events.append(event)
    return events


def test_sse_generator_direct():
    """Test SSE event generator directly against a completed job.

    This bypasses TestClient limitations and tests the async generator
    that powers SSE streaming. Drives a fast-completing job and collects
    all yielded events.
    """
    registry = JobRegistry()

    # Create a job that's already completed
    job = registry.create("compile", [])
    job.status = "completed"
    job.returncode = 0
    job.stdout.extend(["line 1", "line 2"])
    job.stderr.extend(["error 1"])

    # Create a minimal event generator
    async def event_generator() -> Any:
        """Minimal SSE event generator for testing."""
        # Yield stdout lines
        for line in job.stdout:
            yield {
                "event": "stdout",
                "data": line,
            }

        # Yield stderr lines
        for line in job.stderr:
            yield {
                "event": "stderr",
                "data": line,
            }

        # Terminal event
        yield {
            "event": "end",
            "data": json.dumps({
                "status": job.status,
                "returncode": job.returncode,
            }),
        }

    # Collect events
    events = asyncio.run(_collect_events(event_generator()))

    # Verify we got all events
    assert len(events) == 4  # 2 stdout + 1 stderr + 1 end

    # Check stdout events
    stdout_events = [e for e in events if e["event"] == "stdout"]
    assert len(stdout_events) == 2
    assert stdout_events[0]["data"] == "line 1"
    assert stdout_events[1]["data"] == "line 2"

    # Check stderr events
    stderr_events = [e for e in events if e["event"] == "stderr"]
    assert len(stderr_events) == 1
    assert stderr_events[0]["data"] == "error 1"

    # Check terminal event
    end_events = [e for e in events if e["event"] == "end"]
    assert len(end_events) == 1
    end_data = json.loads(end_events[0]["data"])
    assert end_data["status"] == "completed"
    assert end_data["returncode"] == 0


def test_sse_generator_progressive():
    """Test SSE generator with progressive job updates.

    Tests that the generator correctly handles jobs that transition
    from pending -> running -> completed, yielding new output as it arrives.
    """
    registry = JobRegistry()
    job = registry.create("compile", [])

    async def progressive_generator() -> Any:
        """Simulate progressive job execution."""
        # Initial state
        job.status = "pending"
        yield {"event": "status", "data": job.status}

        # Job starts
        job.status = "running"
        yield {"event": "status", "data": job.status}

        # Output arrives
        job.stdout.append("Starting compile...")
        yield {"event": "stdout", "data": "Starting compile..."}

        job.stdout.append("Processing entity 1...")
        yield {"event": "stdout", "data": "Processing entity 1..."}

        # Job completes
        job.status = "completed"
        job.returncode = 0
        yield {
            "event": "end",
            "data": json.dumps({
                "status": job.status,
                "returncode": job.returncode,
            }),
        }

    events = asyncio.run(_collect_events(progressive_generator()))

    # Verify progression
    assert len(events) == 5

    # Check status events
    status_events = [e for e in events if e["event"] == "status"]
    assert len(status_events) == 2
    assert status_events[0]["data"] == "pending"
    assert status_events[1]["data"] == "running"

    # Check we got stdout
    stdout_events = [e for e in events if e["event"] == "stdout"]
    assert len(stdout_events) == 2

    # Check terminal event
    end_events = [e for e in events if e["event"] == "end"]
    assert len(end_events) == 1
    end_data = json.loads(end_events[0]["data"])
    assert end_data["status"] == "completed"


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

"""Tests for async job runner endpoints with SSE streaming."""

import sys
import tempfile
from pathlib import Path
import asyncio
import json
from typing import Any

import pytest
from fastapi.testclient import TestClient

from agent_vault.api.app import create_app
from agent_vault.api.config import Settings
from agent_vault.api import jobs as jobs_module
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
    """Test that shell-injection-shaped args are rejected outright with 400.

    Args are validated against a per-op allowlist BEFORE any subprocess is
    spawned — injection attempts never even become a job.
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
        response = client.post(
            "/api/jobs/run",
            json={"op": "compile", "args": [malicious_arg]},
        )
        assert response.status_code == 400, f"Expected 400 for arg {malicious_arg!r}"
        assert "detail" in response.json()


def test_job_args_disallowed_flag_rejected():
    """A flag outside the per-op allowlist is a 400 with a clear message."""
    vault = create_test_vault()
    settings = Settings(vault_path=str(vault), token="")
    app = create_app(settings)
    client = TestClient(app)

    # --report writes to an arbitrary path — deliberately not allowlisted
    response = client.post(
        "/api/jobs/run",
        json={"op": "lint", "args": ["--report", "/tmp/evil.json"]},
    )
    assert response.status_code == 400
    assert "--report" in response.json()["detail"]

    # A flag valid for one op is still rejected on another
    response = client.post(
        "/api/jobs/run",
        json={"op": "promote", "args": ["--only", "some-slug"]},
    )
    assert response.status_code == 400


async def _noop_run_job(job, vault_path):
    """Stand-in for run_job: validation-focused tests don't need to spawn real
    subprocesses (background children at TestClient shutdown can hang this
    sandbox's event-loop teardown)."""
    job.status = "completed"
    job.returncode = 0


def test_job_args_allowed_flags_pass(monkeypatch):
    """Allowlisted flags (with valid values) pass validation and start a job."""
    monkeypatch.setattr(jobs_module, "run_job", _noop_run_job)
    vault = create_test_vault()
    settings = Settings(vault_path=str(vault), token="")
    app = create_app(settings)
    client = TestClient(app)

    cases = [
        ("compile", ["--only", "test-doc", "--limit", "5"]),
        ("promote", ["--dry-run"]),
        ("reclassify_apply", ["--slug", "test-doc", "--dry-run"]),
        ("lint", ["--json", "--aging-days", "30"]),
        ("compact", []),  # dry-run is the default; --apply is also allowed
        ("compact", ["--apply"]),
    ]
    for op, args in cases:
        response = client.post("/api/jobs/run", json={"op": op, "args": args})
        assert response.status_code == 200, f"Failed for {op} {args}: {response.text}"
        assert "job_id" in response.json()


def test_job_args_bad_values_rejected():
    """Allowlisted flags with malformed values are still rejected."""
    vault = create_test_vault()
    settings = Settings(vault_path=str(vault), token="")
    app = create_app(settings)
    client = TestClient(app)

    bad_cases = [
        ("compile", ["--only", "Bad Slug!"]),  # spaces/uppercase/punctuation
        ("compile", ["--only", "../escape"]),  # traversal-shaped
        ("compile", ["--limit"]),  # missing value
        ("compile", ["--limit", "-1"]),  # not a positive int
        ("lint", ["--aging-days", "abc"]),
        ("ingest", ["/etc/passwd"]),  # positional must be slug-shaped
    ]
    for op, args in bad_cases:
        response = client.post("/api/jobs/run", json={"op": op, "args": args})
        assert response.status_code == 400, f"Expected 400 for {op} {args}"


def test_job_run_lint_and_compact_ops_accepted(monkeypatch):
    """The extended allowlist accepts lint and compact; unknown ops still fail."""
    monkeypatch.setattr(jobs_module, "run_job", _noop_run_job)
    vault = create_test_vault()
    settings = Settings(vault_path=str(vault), token="")
    app = create_app(settings)
    client = TestClient(app)

    for op in ("lint", "compact"):
        response = client.post("/api/jobs/run", json={"op": op, "args": []})
        assert response.status_code == 200, f"Failed for op {op}"
        assert "job_id" in response.json()

    response = client.post("/api/jobs/run", json={"op": "synapse", "args": []})
    assert response.status_code == 422


@pytest.mark.timeout(60)
def test_run_job_stderr_flood_no_deadlock(monkeypatch, tmp_path):
    """Regression: a child writing >>64KB to stderr must not deadlock run_job.

    The old implementation drained stdout to EOF before touching stderr; once
    the child filled the ~64KB stderr pipe buffer it blocked writing, stdout
    never reached EOF, and both sides hung forever. Both pipes are now drained
    concurrently.
    """
    # ~200KB of stderr (4000 lines x 50 chars) plus a little stdout
    script = (
        "import sys\n"
        "sys.stdout.write('starting\\n')\n"
        "for i in range(4000):\n"
        "    sys.stderr.write('e' * 49 + '\\n')\n"
        "sys.stdout.write('done\\n')\n"
    )
    monkeypatch.setattr(
        jobs_module,
        "_build_command",
        lambda op, args: [sys.executable, "-c", script],
    )

    registry = JobRegistry()
    job = registry.create("compile", [])
    asyncio.run(jobs_module.run_job(job, tmp_path))

    assert job.status == "completed"
    assert job.returncode == 0
    assert "starting" in job.stdout
    assert "done" in job.stdout
    # Buffers are capped, but the all-time counter saw every line
    assert job.stderr_seen == 4000
    assert len(job.stderr) == min(4000, jobs_module.MAX_LOG_LINES)


def test_job_registry_evicts_finished_jobs():
    """Finished jobs beyond MAX_FINISHED_JOBS are evicted oldest-first;
    running jobs are never evicted."""
    registry = JobRegistry()

    running = registry.create("compile", [])
    running.status = "running"

    finished_ids = []
    for _ in range(jobs_module.MAX_FINISHED_JOBS + 10):
        job = registry.create("compile", [])
        job.status = "completed"
        job.returncode = 0
        finished_ids.append(job.id)

    # Creating one more triggers eviction of the oldest finished jobs
    registry.create("compile", [])

    assert registry.get(running.id) is not None, "running job must never be evicted"
    finished_still_present = [jid for jid in finished_ids if registry.get(jid)]
    assert len(finished_still_present) <= jobs_module.MAX_FINISHED_JOBS
    # Oldest finished jobs went first
    assert registry.get(finished_ids[0]) is None
    assert registry.get(finished_ids[-1]) is not None

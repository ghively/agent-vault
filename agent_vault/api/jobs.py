"""Async job runner with SSE streaming for long-running vault operations.

Provides subprocess-isolated execution of heavy operations (ingest, compile,
promote, reclassify_apply) with status polling and real-time log streaming via
Server-Sent Events (SSE).
"""

from __future__ import annotations

import asyncio
import os
import sys
import uuid
from collections.abc import AsyncIterable
from dataclasses import dataclass, field
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request, Depends
from fastapi.responses import JSONResponse
from sse_starlette.sse import EventSourceResponse as SSEEventSourceResponse
from pydantic import BaseModel
import json

from agent_vault.api.config import Settings

# Allowed operations - must be validated before subprocess execution
ALLOWED_OPS = {"ingest", "compile", "promote", "reclassify_apply"}


@dataclass
class Job:
    """A subprocess job with status tracking and log buffering."""

    id: str
    op: str
    args: list[str]
    proc: asyncio.subprocess.Process | None = None
    status: str = "pending"  # pending, running, completed, failed
    returncode: int | None = None
    stdout: list[str] = field(default_factory=list)
    stderr: list[str] = field(default_factory=list)


class JobRegistry:
    """In-memory registry for tracking jobs.

    Single-host service - in-memory storage is sufficient. Jobs are stored
    until completion and can be queried by ID.
    """

    def __init__(self) -> None:
        self.jobs: dict[str, Job] = {}

    def create(self, op: str, args: list[str]) -> Job:
        """Create a new job entry.

        Args:
            op: Operation name (must be in ALLOWED_OPS)
            args: Command-line arguments for the operation

        Returns:
            Created Job instance
        """
        job_id = uuid.uuid4().hex[:12]
        job = Job(id=job_id, op=op, args=args)
        self.jobs[job_id] = job
        return job

    def get(self, job_id: str) -> Job | None:
        """Get a job by ID.

        Args:
            job_id: Job identifier

        Returns:
            Job instance or None if not found
        """
        return self.jobs.get(job_id)


# Global registry instance
_registry = JobRegistry()


def get_registry() -> JobRegistry:
    """Get the global job registry.

    Returns:
        Global JobRegistry instance
    """
    return _registry


class JobRunRequest(BaseModel):
    """Request body for job execution."""

    op: str
    args: list[str] = []


def _build_command(op: str, args: list[str]) -> list[str]:
    """Build subprocess command for an operation.

    Args:
        op: Operation name (validated against ALLOWED_OPS)
        args: Additional arguments for the operation

    Returns:
        Command argv list for subprocess execution

    Raises:
        ValueError: If operation is not in allowlist
    """
    if op not in ALLOWED_OPS:
        raise ValueError(f"Operation {op!r} not in allowlist")

    # Map operation names to module names
    module_map = {
        "ingest": "agent_vault.ingest",
        "compile": "agent_vault.compiler",
        "promote": "agent_vault.promote",
        "reclassify_apply": "agent_vault.reclassify_apply",
    }

    module = module_map.get(op)
    if not module:
        # This should never happen due to allowlist check
        raise ValueError(f"Unknown operation: {op!r}")

    return [sys.executable, "-m", module, *args]


def _vault_env(vault_path: Path) -> dict[str, str]:
    """Build environment dict for subprocess execution.

    Always pins AGENT_VAULT_PATH to prevent callers from redirecting it.
    Mirrors SynapseNAS server/actions.py:_vault_env.

    Args:
        vault_path: Path to the vault directory

    Returns:
        Environment dictionary for subprocess
    """
    env = dict(os.environ)
    env["AGENT_VAULT_PATH"] = str(vault_path)
    return env


async def run_job(job: Job, vault_path: Path) -> None:
    """Execute a job subprocess and capture output.

    Args:
        job: Job instance to execute
        vault_path: Vault directory path for subprocess execution
    """
    job.status = "running"
    cmd = _build_command(job.op, job.args)
    env = _vault_env(vault_path)

    try:
        # Create subprocess with isolated environment
        # IMPORTANT: No shell=True - argv list prevents shell injection
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            cwd=str(vault_path),
            env=env,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )

        job.proc = proc

        # Capture stdout and decode manually
        if proc.stdout:
            async for raw_line in proc.stdout:
                line = raw_line.decode(errors="replace").rstrip("\n")
                job.stdout.append(line)

        # Capture stderr and decode manually
        if proc.stderr:
            async for raw_line in proc.stderr:
                line = raw_line.decode(errors="replace").rstrip("\n")
                job.stderr.append(line)

        # Wait for process completion
        await proc.wait()
        job.returncode = proc.returncode
        job.status = "completed" if proc.returncode == 0 else "failed"

    except Exception as e:
        # Handle any subprocess errors
        job.stderr.append(f"Job failed: {type(e).__name__}: {e}")
        job.returncode = job.returncode if job.returncode is not None else -1
        job.status = "failed"


# Create router
router = APIRouter()


async def get_settings(request: Request) -> Settings:
    """Get settings from app state (dependency injection)."""
    return request.app.state.settings  # type: ignore


@router.post("/jobs/run")
async def run_job_endpoint(
    request: JobRunRequest,
    settings: Settings = Depends(get_settings),
) -> JSONResponse:
    """Start a new job subprocess.

    Args:
        request: Job run request with operation name and arguments
        settings: Application settings (injected via FastAPI)

    Returns:
        JSON response with job_id and initial status

    Raises:
        HTTPException: 422 if operation not in allowlist
    """
    # Validate operation against allowlist
    if request.op not in ALLOWED_OPS:
        raise HTTPException(
            status_code=422,
            detail=f"Operation {request.op!r} not allowed. Must be one of: {sorted(ALLOWED_OPS)}"
        )

    registry = get_registry()
    job = registry.create(request.op, request.args)

    # Get vault path from settings
    vault_path = Path(settings.vault_path)

    # Start job in background
    asyncio.create_task(run_job(job, vault_path))

    return JSONResponse(
        content={
            "job_id": job.id,
            "status": job.status,
        }
    )


@router.get("/jobs/{job_id}")
async def get_job_status(job_id: str) -> JSONResponse:
    """Get current status of a job.

    Args:
        job_id: Job identifier

    Returns:
        JSON response with status, returncode, and output tails

    Raises:
        HTTPException: 404 if job not found
    """
    registry = get_registry()
    job = registry.get(job_id)

    if not job:
        raise HTTPException(status_code=404, detail=f"Job {job_id} not found")

    # Return last 100 lines of stdout/stderr to avoid huge responses
    stdout_tail = job.stdout[-100:] if job.stdout else []
    stderr_tail = job.stderr[-100:] if job.stderr else []

    return JSONResponse(
        content={
            "job_id": job.id,
            "status": job.status,
            "returncode": job.returncode,
            "stdout": stdout_tail,
            "stderr": stderr_tail,
        }
    )


@router.get("/jobs/{job_id}/stream")
async def stream_job(job_id: str, request: Request) -> SSEEventSourceResponse:
    """Stream job progress via Server-Sent Events (SSE).

    Yields status events as the job progresses, ending with a terminal event
    when the job completes or fails.

    Args:
        job_id: Job identifier
        request: FastAPI request object for disconnect detection

    Returns:
        SSE response with status events

    Raises:
        HTTPException: 404 if job not found
    """
    registry = get_registry()
    job = registry.get(job_id)

    if not job:
        raise HTTPException(status_code=404, detail=f"Job {job_id} not found")

    async def event_generator() -> AsyncIterable[dict[str, str]]:
        """Generate SSE events for job progress."""
        last_stdout_len = 0
        last_stderr_len = 0

        while True:
            # Check if client disconnected
            if await request.is_disconnected():
                break

            # Yield new stdout lines
            if len(job.stdout) > last_stdout_len:
                for line in job.stdout[last_stdout_len:]:
                    yield {
                        "event": "stdout",
                        "data": line,
                    }
                last_stdout_len = len(job.stdout)

            # Yield new stderr lines
            if len(job.stderr) > last_stderr_len:
                for line in job.stderr[last_stderr_len:]:
                    yield {
                        "event": "stderr",
                        "data": line,
                    }
                last_stderr_len = len(job.stderr)

            # Check if job is terminal
            if job.status in ("completed", "failed"):
                yield {
                    "event": "end",
                    "data": json.dumps(
                        {
                            "status": job.status,
                            "returncode": job.returncode,
                        }
                    ),
                }
                break

            # Wait before next poll
            await asyncio.sleep(0.2)

    return SSEEventSourceResponse(event_generator())

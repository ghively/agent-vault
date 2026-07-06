"""Async job runner with SSE streaming for long-running vault operations.

Provides subprocess-isolated execution of heavy operations (ingest, compile,
promote, reclassify_apply) with status polling and real-time log streaming via
Server-Sent Events (SSE).
"""

from __future__ import annotations

import asyncio
import os
import re
import sys
import time
import uuid
from collections import deque
from collections.abc import AsyncIterable, Callable
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse
from sse_starlette.sse import EventSourceResponse as SSEEventSourceResponse
from pydantic import BaseModel
import json

from agent_vault.api.config import Settings

# Allowed operations - must be validated before subprocess execution
ALLOWED_OPS = {"ingest", "compile", "promote", "reclassify_apply", "lint", "compact"}

# Per-job log buffers are capped so a chatty child can't grow memory unbounded.
MAX_LOG_LINES = 2000
# Finished (completed/failed) jobs beyond this many are evicted, oldest first.
# Running/pending jobs are never evicted.
MAX_FINISHED_JOBS = 50

# --- args allowlist ----------------------------------------------------------
# Conservative value patterns. Anything not matching is rejected with 400 —
# args are forwarded to `python -m agent_vault.<module>` so we never pass
# arbitrary flags/paths from the network into the CLIs.
_SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,127}$")
_POSINT_RE = re.compile(r"^[1-9][0-9]{0,5}$")
_MODEL_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")

# op -> {flag: value-pattern or None (boolean flag, takes no value)}
# Derived from each module's CLI (see agent_vault/<module>.py main()):
#   ingest            — no flags (positional vault only)
#   compiler          — --limit N, --only <slug>, --model <name>
#   promote           — --dry-run
#   reclassify_apply  — --slug <slug>, --dry-run
#   lint              — --json, --aging-days N   (--report writes files: NOT allowed)
#   compact           — --apply
OP_FLAGS: dict[str, dict[str, re.Pattern[str] | None]] = {
    "ingest": {},
    "compile": {"--limit": _POSINT_RE, "--only": _SLUG_RE, "--model": _MODEL_RE},
    "promote": {"--dry-run": None},
    "reclassify_apply": {"--slug": _SLUG_RE, "--dry-run": None},
    "lint": {"--json": None, "--aging-days": _POSINT_RE},
    "compact": {"--apply": None},
}


def validate_args(op: str, args: list[str]) -> None:
    """Validate job args against the per-op flag allowlist.

    Raises:
        HTTPException: 400 with a clear message for any disallowed flag,
            malformed flag value, or unsafe positional argument.
    """
    allowed = OP_FLAGS.get(op, {})
    i = 0
    while i < len(args):
        a = args[i]
        if a.startswith("-"):
            if a not in allowed:
                raise HTTPException(
                    status_code=400,
                    detail=(
                        f"Flag {a!r} not allowed for op {op!r}. "
                        f"Allowed flags: {sorted(allowed) or 'none'}"
                    ),
                )
            pattern = allowed[a]
            if pattern is not None:
                if i + 1 >= len(args):
                    raise HTTPException(
                        status_code=400,
                        detail=f"Flag {a!r} requires a value",
                    )
                value = args[i + 1]
                if not pattern.match(value):
                    raise HTTPException(
                        status_code=400,
                        detail=f"Invalid value {value!r} for flag {a!r}",
                    )
                i += 2
                continue
        else:
            # Positional values (e.g. slugs) must be conservative — no paths,
            # no leading dashes/dots, nothing shell- or traversal-shaped.
            if not _SLUG_RE.match(a):
                raise HTTPException(
                    status_code=400,
                    detail=(
                        f"Positional argument {a!r} rejected: must match "
                        f"{_SLUG_RE.pattern}"
                    ),
                )
        i += 1


@dataclass
class Job:
    """A subprocess job with status tracking and log buffering.

    stdout/stderr are bounded deques (last MAX_LOG_LINES lines each);
    stdout_seen/stderr_seen count every line ever appended so streaming
    consumers can detect new output even after old lines are evicted.

    ``vault`` is the vault name this job runs against (MTAV §6a.1a). It's set
    at creation time and used by GET/DELETE/stream to enforce that only the
    job's owner (a token with any grant on that vault) can access it.
    """

    id: str
    op: str
    args: list[str]
    vault: str = ""  # MTAV: which vault this job belongs to
    proc: asyncio.subprocess.Process | None = None
    status: str = "pending"  # pending, running, completed, failed
    returncode: int | None = None
    stdout: deque[str] = field(default_factory=lambda: deque(maxlen=MAX_LOG_LINES))
    stderr: deque[str] = field(default_factory=lambda: deque(maxlen=MAX_LOG_LINES))
    stdout_seen: int = 0
    stderr_seen: int = 0

    def add_stdout(self, line: str) -> None:
        """Append a stdout line, tracking the all-time line count."""
        self.stdout.append(line)
        self.stdout_seen += 1

    def add_stderr(self, line: str) -> None:
        """Append a stderr line, tracking the all-time line count."""
        self.stderr.append(line)
        self.stderr_seen += 1


class JobRegistry:
    """In-memory registry for tracking jobs.

    Single-host service - in-memory storage is sufficient. Jobs are stored
    until completion and can be queried by ID.
    """

    def __init__(self) -> None:
        self.jobs: dict[str, Job] = {}

    def _evict_finished(self) -> None:
        """Drop the oldest finished jobs beyond MAX_FINISHED_JOBS.

        Never evicts pending/running jobs. Relies on dict insertion order
        (oldest jobs first).
        """
        finished = [j for j in self.jobs.values() if j.status in ("completed", "failed")]
        excess = len(finished) - MAX_FINISHED_JOBS
        for job in finished[:excess] if excess > 0 else []:
            self.jobs.pop(job.id, None)

    def create(self, op: str, args: list[str], vault: str = "") -> Job:
        """Create a new job entry, evicting old finished jobs first.

        Args:
            op: Operation name (must be in ALLOWED_OPS)
            args: Command-line arguments for the operation
            vault: Vault name this job runs against (MTAV ownership)

        Returns:
            Created Job instance
        """
        self._evict_finished()
        job_id = uuid.uuid4().hex[:12]
        job = Job(id=job_id, op=op, args=args, vault=vault)
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

# Strong references to in-flight job tasks. asyncio keeps only a WEAK reference


def _check_job_vault_access(job: Job, request: Request) -> None:
    """MTAV §6a.1a: verify the caller has a grant on the job's vault.

    In legacy single-vault mode (no ``vault_name`` on request state), this is
    a no-op. In MTAV mode, the caller's identity must have any grant on
    ``job.vault``, else 403. System/bootstrap tokens bypass the check.
    """
    ident = getattr(request.state, "identity", None)
    if ident is None:
        return  # no identity resolved (e.g. auth disabled) — allow
    # Legacy mode: identity has _legacy_scopes, no per-vault grants → skip
    if ident._legacy_scopes:  # type: ignore[attr-defined] # noqa: SLF001
        return
    if ident.system:  # type: ignore[attr-defined]
        return
    if not job.vault:
        return  # job created before MTAV — no ownership to check
    if not ident.has_scope(job.vault, "read") and not any(
        g.matches_vault(job.vault) for g in ident.grants
    ):
        # SI-5: return 403 with a generic message (no existence oracle)
        raise HTTPException(status_code=403, detail="access denied")


# Strong references to in-flight job tasks. asyncio keeps only a WEAK reference
# to a bare create_task() result, so without this a running job can be
# garbage-collected mid-execution. We hold the task until it finishes, then let
# the done-callback drop it. See docs/AUDIT.md D2.
_background_tasks: set[asyncio.Task[None]] = set()


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
        "lint": "agent_vault.lint",
        "compact": "agent_vault.compact",
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


def _record_run(job: Job, vault_path: Path, duration_s: int) -> None:
    """Append one job-completion record to discovery/_runs.jsonl.

    API-triggered runs are a third invocation path alongside the .sh cadences
    and run_cadence.py; without this, GET /api/runs and the dashboard's
    last_run silently omit every run started from the web UI. The record shape
    matches cadences/run_cadence.py:record_run ({cadence, ts, rc, duration_s,
    detail}) so history.py and status.py read it unchanged; source/job_id are
    additive so a web run is still distinguishable. Best-effort: never raises
    into the job coroutine. See docs/AUDIT.md D2.
    """
    try:
        discovery = vault_path / "discovery"
        discovery.mkdir(exist_ok=True)
        ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        detail = "ok"
        if job.status == "failed":
            tail = job.stderr[-1] if job.stderr else f"rc={job.returncode}"
            detail = tail[:200]
        rec = {
            "cadence": job.op,
            "ts": ts,
            "rc": job.returncode if job.returncode is not None else -1,
            "duration_s": duration_s,
            "detail": detail,
            "source": "api-job",
            "job_id": job.id,
        }
        with open(discovery / "_runs.jsonl", "a", encoding="utf-8") as fh:
            fh.write(json.dumps(rec) + "\n")
    except Exception:  # noqa: BLE001 - history recording must never abort a job
        pass


async def run_job(job: Job, vault_path: Path) -> None:
    """Execute a job subprocess and capture output.

    Args:
        job: Job instance to execute
        vault_path: Vault directory path for subprocess execution
    """
    job.status = "running"
    cmd = _build_command(job.op, job.args)
    env = _vault_env(vault_path)
    started = time.monotonic()

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

        async def _drain(stream: asyncio.StreamReader | None, sink: Callable[[str], None]) -> None:
            """Read one pipe line-by-line to EOF, decoding as we go."""
            if stream is None:
                return
            async for raw_line in stream:
                sink(raw_line.decode(errors="replace").rstrip("\n"))

        # Drain BOTH pipes concurrently. Reading them sequentially deadlocks:
        # a child that fills the stderr pipe buffer (~64KB) blocks writing
        # while we block reading stdout to EOF — neither side progresses.
        await asyncio.gather(
            _drain(proc.stdout, job.add_stdout),
            _drain(proc.stderr, job.add_stderr),
        )

        # Wait for process completion
        await proc.wait()
        job.returncode = proc.returncode
        job.status = "completed" if proc.returncode == 0 else "failed"

    except Exception as e:
        # Handle any subprocess errors
        job.add_stderr(f"Job failed: {type(e).__name__}: {e}")
        job.returncode = job.returncode if job.returncode is not None else -1
        job.status = "failed"
    finally:
        # Record every terminal outcome (success OR failure) to the shared run
        # history, so the web-triggered path honors the same _runs.jsonl
        # contract the cadence paths do.
        _record_run(job, vault_path, int(time.monotonic() - started))


# Create router
router = APIRouter()


async def get_settings(request: Request) -> Settings:
    """Get settings from app state (dependency injection).

    Returns a Settings with vault_path resolved from request state (MTAV).
    """
    s = request.app.state.settings  # type: ignore
    vault_path = getattr(request.state, "vault_path", "") or ""
    if vault_path and vault_path != s.vault_path:
        from dataclasses import replace
        return replace(s, vault_path=vault_path)
    return s


@router.post("/jobs/run")
async def run_job_endpoint(
    request: Request,
    job_request: JobRunRequest,
) -> JSONResponse:
    """Start a new job subprocess.

    Args:
        request: FastAPI request (for resolved vault_path from auth dep)
        job_request: Job run request with operation name and arguments

    Returns:
        JSON response with job_id and initial status

    Raises:
        HTTPException: 422 if operation not in allowlist, 400 if any arg
            is outside the per-op flag/value allowlist
    """
    # Validate operation against allowlist
    if job_request.op not in ALLOWED_OPS:
        raise HTTPException(
            status_code=422,
            detail=f"Operation {job_request.op!r} not allowed. Must be one of: {sorted(ALLOWED_OPS)}"
        )

    # Validate args against the per-op allowlist (raises 400)
    validate_args(job_request.op, job_request.args)

    # Resolve vault path from request state (set by auth dependency)
    # In legacy mode, this is settings.vault_path; in MTAV mode, the resolved vault.
    vault_path_str = getattr(request.state, "vault_path", "") or ""
    vault_name = getattr(request.state, "vault_name", "") or ""
    if not vault_path_str:
        settings = request.app.state.settings
        vault_path_str = settings.vault_path
    vault_path = Path(vault_path_str)

    registry = get_registry()
    job = registry.create(job_request.op, job_request.args, vault=vault_name)

    # Start job in background, holding a strong reference so the task can't be
    # garbage-collected before it completes (see _background_tasks).
    task = asyncio.create_task(run_job(job, vault_path))
    _background_tasks.add(task)
    task.add_done_callback(_background_tasks.discard)

    return JSONResponse(
        content={
            "job_id": job.id,
            "status": job.status,
        }
    )


@router.get("/jobs/{job_id}")
async def get_job_status(job_id: str, request: Request) -> JSONResponse:
    """Get current status of a job.

    Args:
        job_id: Job identifier
        request: FastAPI request (for identity check, MTAV §6a.1a)

    Returns:
        JSON response with status, returncode, and output tails

    Raises:
        HTTPException: 404 if job not found, 403 if the caller has no grant
            on the job's vault (MTAV cross-tenant isolation, §6a.1a)
    """
    registry = get_registry()
    job = registry.get(job_id)

    if not job:
        raise HTTPException(status_code=404, detail=f"Job {job_id} not found")

    # MTAV §6a.1a: cross-tenant isolation — only the job's owner vault can access it
    _check_job_vault_access(job, request)

    # Return last 100 lines of stdout/stderr to avoid huge responses
    stdout_tail = list(job.stdout)[-100:]
    stderr_tail = list(job.stderr)[-100:]

    return JSONResponse(
        content={
            "job_id": job.id,
            "status": job.status,
            "returncode": job.returncode,
            "stdout": stdout_tail,
            "stderr": stderr_tail,
        }
    )


@router.delete("/jobs/{job_id}")
async def cancel_job(job_id: str, request: Request) -> JSONResponse:
    """Cancel a running job by terminating its subprocess (B7).

    404 if the job is unknown. A job that already finished is returned as-is
    (idempotent — cancelling a completed job is a no-op, not an error).

    MTAV §6a.1a: 403 if the caller has no grant on the job's vault.
    """
    registry = get_registry()
    job = registry.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail=f"Job {job_id} not found")

    _check_job_vault_access(job, request)

    if job.status in ("completed", "failed"):
        return JSONResponse(content={"job_id": job.id, "status": job.status,
                                     "cancelled": False})
    proc = job.proc
    if proc is not None and proc.returncode is None:
        try:
            proc.terminate()
        except ProcessLookupError:
            pass  # already exited between the check and here
    job.status = "failed"
    if job.returncode is None:
        job.returncode = -1
    job.add_stderr("job cancelled by request")
    return JSONResponse(content={"job_id": job.id, "status": job.status,
                                 "cancelled": True})


@router.get("/jobs/{job_id}/stream")
async def stream_job(job_id: str, request: Request) -> SSEEventSourceResponse:
    """Stream job progress via Server-Sent Events (SSE).

    Yields status events as the job progresses, ending with a terminal event
    when the job completes or fails.

    Args:
        job_id: Job identifier
        request: FastAPI request object for disconnect detection + identity

    Returns:
        SSE response with status events

    Raises:
        HTTPException: 404 if job not found, 403 if caller lacks grant on
            the job's vault (MTAV §6a.1a)
    """
    registry = get_registry()
    job = registry.get(job_id)

    if not job:
        raise HTTPException(status_code=404, detail=f"Job {job_id} not found")

    _check_job_vault_access(job, request)

    async def event_generator() -> AsyncIterable[dict[str, str]]:
        """Generate SSE events for job progress.

        Tracks the all-time line counters (stdout_seen/stderr_seen) rather than
        buffer length: the buffers are bounded deques, so counting evicted
        lines by len() alone would miss output on very chatty jobs.
        """
        last_stdout_seen = 0
        last_stderr_seen = 0

        while True:
            # Check if client disconnected
            if await request.is_disconnected():
                break

            # Yield new stdout lines (bounded by what's still buffered).
            # Snapshot counter + buffer before yielding: each yield suspends
            # this coroutine, so the job task may append more lines meanwhile.
            seen = job.stdout_seen
            if seen > last_stdout_seen:
                buf = list(job.stdout)
                for line in buf[-min(seen - last_stdout_seen, len(buf)):]:
                    yield {
                        "event": "stdout",
                        "data": line,
                    }
                last_stdout_seen = seen

            # Yield new stderr lines (same snapshot discipline)
            seen = job.stderr_seen
            if seen > last_stderr_seen:
                buf = list(job.stderr)
                for line in buf[-min(seen - last_stderr_seen, len(buf)):]:
                    yield {
                        "event": "stderr",
                        "data": line,
                    }
                last_stderr_seen = seen

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

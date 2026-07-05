"""Review action endpoints (approve/reject proposals + entities).

POST /api/review/proposals/{pid}/approve|reject — Approve or reject a queued proposal
POST /api/review/entities/{ref:path}/approve|reject — Approve or reject a needs-review entity

All endpoints mirror SynapseNAS server/actions.py shapes but run in-process.
The underlying agent_vault.review operations take the vault-wide write lock
themselves, so these handlers must NOT wrap them in a second lock (flock is not
re-entrant across two open()s in one process — an outer lock self-deadlocks).
The one exception is reject_proposal, whose cmd_reject does not lock, so its
handler holds the lock. Approvals/rejections trigger a reindex afterward to
keep the search index current.

Security: Imports ONLY from the trusted agent_vault package. NEVER inserts
AGENT_VAULT_PATH or the vault data dir onto sys.path, and NEVER imports a module
by bare name that would resolve from the data dir. This prevents RCE via
malicious vault content.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

from agent_vault.api.config import Settings

# Import ONLY from the trusted agent_vault package
from agent_vault import review as review_module
from agent_vault.locking import vault_lock, LockTimeout

router = APIRouter()

# Validation regexes (mirrored from SynapseNAS server/actions.py)
_PID_RE = re.compile(r"^[0-9a-f]{8}$")  # short_id (sha256[:8])
_REF_RE = re.compile(r"^[a-z0-9][a-z0-9-]*/[a-z0-9][a-z0-9._-]{0,127}$")  # type/slug


def _valid_pid(s: str) -> bool:
    """Validate proposal ID format (8 hex characters)."""
    return bool(isinstance(s, str) and _PID_RE.match(s))


def _valid_ref(s: str) -> bool:
    """Validate entity ref format (type/slug)."""
    return bool(isinstance(s, str) and _REF_RE.match(s) and ".." not in s)


async def get_settings(request: Request) -> Settings:
    """Get settings from app state (dependency injection)."""
    return request.app.state.settings  # type: ignore


class ReasonRequest(BaseModel):
    """Optional reason for approval/rejection."""
    reason: str = ""


@router.post("/review/proposals/{pid}/approve")
async def approve_proposal(
    pid: str,
    request: Request,
    reason_req: ReasonRequest = ReasonRequest(),
    s: Settings = Depends(get_settings),
) -> dict[str, Any]:
    """Approve a queued proposal under the vault-wide write lock.

    After successful approval, the vault index is rebuilt to keep the search
    index current. This mirrors SynapseNAS server/actions.py behavior.

    Args:
        pid: Proposal short ID (8 hex characters)
        reason_req: Optional reason for approval
        s: Settings (dependency injected)

    Returns:
        Dict with {"ok": bool, "code": int, "stdout": str, "stderr": str}

    Raises:
        HTTPException: 422 for invalid pid, 503 if lock timeout
    """
    if not _valid_pid(pid):
        raise HTTPException(status_code=422, detail="invalid proposal id format")

    vault = Path(s.vault_path)
    reason = reason_req.reason or ""

    try:
        # NOTE: no outer vault_lock here. review.cmd_approve takes the vault
        # write lock itself (in each mutating branch; the reclassify branch
        # delegates to apply_all, which also locks). flock is NOT re-entrant
        # across two open()s in one process, so an outer lock would self-deadlock
        # until the lock timeout (default 600s) and then 503. Same posture as
        # creds.py:recompile / edit.py:reclassify.
        exit_code = review_module.cmd_approve(  # type: ignore[no-untyped-call]
            str(vault), pid, reason
        )

        # Rebuild the search index after the mutation. build_index is a reader
        # (it does not take the write lock), so running it unlocked here is safe.
        from agent_vault.ingest import refresh_index
        refresh_index(str(vault))  # type: ignore[no-untyped-call]

        return {
            "ok": exit_code == 0,
            "code": exit_code,
            "stdout": "",
            "stderr": ""
        }

    except SystemExit as e:
        # review.cmd_approve uses sys.exit on errors
        return {
            "ok": False,
            "code": e.code if isinstance(e.code, int) else 1,
            "stdout": "",
            "stderr": str(e)
        }
    except LockTimeout:
        raise HTTPException(status_code=503, detail="vault is locked by another operation")
    except Exception as e:
        return {
            "ok": False,
            "code": 1,
            "stdout": "",
            "stderr": str(e)
        }


@router.post("/review/proposals/{pid}/reject")
async def reject_proposal(
    pid: str,
    request: Request,
    reason_req: ReasonRequest = ReasonRequest(),
    s: Settings = Depends(get_settings),
) -> dict[str, Any]:
    """Reject a queued proposal under the vault-wide write lock.

    Args:
        pid: Proposal short ID (8 hex characters)
        reason_req: Optional reason for rejection
        s: Settings (dependency injected)

    Returns:
        Dict with {"ok": bool, "code": int, "stdout": str, "stderr": str}

    Raises:
        HTTPException: 422 for invalid pid, 503 if lock timeout
    """
    if not _valid_pid(pid):
        raise HTTPException(status_code=422, detail="invalid proposal id format")

    vault = Path(s.vault_path)
    reason = reason_req.reason or ""

    try:
        # Acquire vault-wide write lock for the rejection
        with vault_lock(str(vault)):
            # Call the review module's reject function in-process
            exit_code = review_module.cmd_reject(  # type: ignore[no-untyped-call]
                str(vault), pid, reason
            )

            return {
                "ok": exit_code == 0,
                "code": exit_code,
                "stdout": "",
                "stderr": ""
            }

    except SystemExit as e:
        # review.cmd_reject uses sys.exit on errors
        return {
            "ok": False,
            "code": e.code if isinstance(e.code, int) else 1,
            "stdout": "",
            "stderr": str(e)
        }
    except LockTimeout:
        raise HTTPException(status_code=503, detail="vault is locked by another operation")
    except Exception as e:
        return {
            "ok": False,
            "code": 1,
            "stdout": "",
            "stderr": str(e)
        }


@router.post("/review/entities/{ref:path}/approve")
async def approve_entity(
    ref: str,
    request: Request,
    reason_req: ReasonRequest = ReasonRequest(),
    s: Settings = Depends(get_settings),
) -> dict[str, Any]:
    """Approve a needs-review entity under the vault-wide write lock.

    Approving an entity moves it from "needs-review" to either "stub" (if no prose)
    or "compiled" (if prose already present). This mirrors SynapseNAS behavior.

    Args:
        ref: Entity reference (type/slug format)
        reason_req: Optional reason for approval
        s: Settings (dependency injected)

    Returns:
        Dict with {"ok": bool, "code": int, "stdout": str, "stderr": str}

    Raises:
        HTTPException: 422 for invalid ref, 503 if lock timeout
    """
    if not _valid_ref(ref):
        raise HTTPException(status_code=422, detail="invalid entity ref format")

    vault = Path(s.vault_path)
    reason = reason_req.reason or ""

    try:
        # No outer vault_lock: cmd_approve_entity takes the write lock itself.
        # An outer lock would self-deadlock (flock is not re-entrant across two
        # open()s in one process). See approve_proposal for the full rationale.
        exit_code = review_module.cmd_approve_entity(  # type: ignore[no-untyped-call]
            str(vault), ref, reason
        )

        # Rebuild the search index after the mutation (reader, safe unlocked).
        from agent_vault.ingest import refresh_index
        refresh_index(str(vault))  # type: ignore[no-untyped-call]

        return {
            "ok": exit_code == 0,
            "code": exit_code,
            "stdout": "",
            "stderr": ""
        }

    except SystemExit as e:
        # review.cmd_approve_entity uses sys.exit on errors
        return {
            "ok": False,
            "code": e.code if isinstance(e.code, int) else 1,
            "stdout": "",
            "stderr": str(e)
        }
    except LockTimeout:
        raise HTTPException(status_code=503, detail="vault is locked by another operation")
    except Exception as e:
        return {
            "ok": False,
            "code": 1,
            "stdout": "",
            "stderr": str(e)
        }


@router.post("/review/entities/{ref:path}/reject")
async def reject_entity(
    ref: str,
    request: Request,
    reason_req: ReasonRequest = ReasonRequest(),
    s: Settings = Depends(get_settings),
) -> dict[str, Any]:
    """Reject a needs-review entity under the vault-wide write lock.

    Rejecting an entity moves it from "needs-review" to "archived" status.

    Args:
        ref: Entity reference (type/slug format)
        reason_req: Optional reason for rejection
        s: Settings (dependency injected)

    Returns:
        Dict with {"ok": bool, "code": int, "stdout": str, "stderr": str}

    Raises:
        HTTPException: 422 for invalid ref, 503 if lock timeout
    """
    if not _valid_ref(ref):
        raise HTTPException(status_code=422, detail="invalid entity ref format")

    vault = Path(s.vault_path)
    reason = reason_req.reason or ""

    try:
        # No outer vault_lock: cmd_reject_entity takes the write lock itself.
        # An outer lock would self-deadlock (flock is not re-entrant across two
        # open()s in one process). See approve_proposal for the full rationale.
        exit_code = review_module.cmd_reject_entity(  # type: ignore[no-untyped-call]
            str(vault), ref, reason
        )

        # Rejection flips the entity's status to `archived`, so rebuild the
        # index to keep review/status/search current (mirrors approve_entity;
        # build_index is a reader, safe to run unlocked).
        from agent_vault.ingest import refresh_index
        refresh_index(str(vault))  # type: ignore[no-untyped-call]

        return {
            "ok": exit_code == 0,
            "code": exit_code,
            "stdout": "",
            "stderr": ""
        }

    except SystemExit as e:
        # review.cmd_reject_entity uses sys.exit on errors
        return {
            "ok": False,
            "code": e.code if isinstance(e.code, int) else 1,
            "stdout": "",
            "stderr": str(e)
        }
    except LockTimeout:
        raise HTTPException(status_code=503, detail="vault is locked by another operation")
    except Exception as e:
        return {
            "ok": False,
            "code": 1,
            "stdout": "",
            "stderr": str(e)
        }

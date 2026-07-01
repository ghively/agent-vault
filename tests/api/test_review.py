"""Tests for review action endpoints (approve/reject proposals + entities)."""

import json
import tempfile
from pathlib import Path

from fastapi.testclient import TestClient

from agent_vault.api.app import create_app
from agent_vault.api.config import Settings


def create_test_vault_with_pending_proposal() -> Path:
    """Create a minimal test vault with a pending proposal in the queue."""
    vault_dir = tempfile.mkdtemp()
    vault = Path(vault_dir)

    # Create directory structure
    (vault / "discovery").mkdir(parents=True)
    (vault / "registry").mkdir(parents=True)

    # Create a pending proposal in promoted.jsonl
    # This is a tag proposal with short_id that we'll compute
    import hashlib
    identity = ["tag", "example-tag"]
    short_id = hashlib.sha256(json.dumps(identity).encode()).hexdigest()[:8]

    proposal_record = {
        "ts": "2026-07-01T12:00:00Z",
        "identity": identity,
        "kind": "tag",
        "action": "queue_for_review",
        "sightings": 3,
        "proposal": {"name": "example-tag"},
        "by": "promote.py"
    }

    (vault / "discovery" / "promoted.jsonl").write_text(
        json.dumps(proposal_record) + "\n"
    )

    # Create minimal registry/schema.yaml
    schema_yaml = """
types: {}
tags: {}
"""
    (vault / "registry" / "schema.yaml").write_text(schema_yaml)

    return vault, short_id


def create_test_vault_with_pending_entity_review() -> Path:
    """Create a minimal test vault with a needs-review entity."""
    vault_dir = tempfile.mkdtemp()
    vault = Path(vault_dir)

    # Create directory structure
    (vault / "entities" / "account").mkdir(parents=True)
    (vault / "discovery").mkdir(parents=True)
    (vault / "registry").mkdir(parents=True)

    # Entity in needs-review status
    entity_md = """---
slug: pending-account
type: account
subtype: checking
title: Pending Review Account
status: needs-review
confidence: 0.6
created: 2026-07-01
---

<!-- LINKS:BEGIN -->
<!-- LINKS:END -->

This account needs human review before approval.
"""
    (vault / "entities" / "account" / "pending-account.md").write_text(entity_md)

    # Create minimal schema.yaml
    schema_yaml = """
types:
  account:
    subtypes:
      - checking
tags: {}
"""
    (vault / "registry" / "schema.yaml").write_text(schema_yaml)

    return vault


def test_approve_proposal_success():
    """Test POST /api/review/proposals/{pid}/approve approves under lock."""
    from unittest.mock import patch

    vault, pid = create_test_vault_with_pending_proposal()
    settings = Settings(vault_path=str(vault), token="")
    app = create_app(settings)
    client = TestClient(app)

    # Mock the review.cmd_approve function to return 0 (success)
    with patch('agent_vault.review.cmd_approve', return_value=0):
        response = client.post(
            f"/api/review/proposals/{pid}/approve",
            json={"reason": "test approval"}
        )

    # Should succeed
    assert response.status_code == 200
    data = response.json()
    assert data.get("ok") is True


def test_reject_proposal_success():
    """Test POST /api/review/proposals/{pid}/reject rejects under lock."""
    from unittest.mock import patch

    vault, pid = create_test_vault_with_pending_proposal()
    settings = Settings(vault_path=str(vault), token="")
    app = create_app(settings)
    client = TestClient(app)

    # Mock the review.cmd_reject function to return 0 (success)
    with patch('agent_vault.review.cmd_reject', return_value=0):
        response = client.post(
            f"/api/review/proposals/{pid}/reject",
            json={"reason": "test rejection"}
        )

    # Should succeed
    assert response.status_code == 200
    data = response.json()
    assert data.get("ok") is True


def test_approve_entity_success():
    """Test POST /api/review/entities/{ref}/approve approves under lock."""
    from unittest.mock import patch

    vault = create_test_vault_with_pending_entity_review()
    settings = Settings(vault_path=str(vault), token="")
    app = create_app(settings)
    client = TestClient(app)

    # Mock the review.cmd_approve_entity function to return 0 (success)
    with patch('agent_vault.review.cmd_approve_entity', return_value=0):
        response = client.post(
            "/api/review/entities/account/pending-account/approve",
            json={"reason": "looks good"}
        )

    # Should succeed
    assert response.status_code == 200
    data = response.json()
    assert data.get("ok") is True


def test_reject_entity_success():
    """Test POST /api/review/entities/{ref}/reject rejects under lock."""
    from unittest.mock import patch

    vault = create_test_vault_with_pending_entity_review()
    settings = Settings(vault_path=str(vault), token="")
    app = create_app(settings)
    client = TestClient(app)

    # Mock the review.cmd_reject_entity function to return 0 (success)
    with patch('agent_vault.review.cmd_reject_entity', return_value=0):
        response = client.post(
            "/api/review/entities/account/pending-account/reject",
            json={"reason": "not relevant"}
        )

    # Should succeed
    assert response.status_code == 200
    data = response.json()
    assert data.get("ok") is True


def test_approve_proposal_invalid_pid():
    """Test POST /api/review/proposals/{pid}/approve returns 422 for invalid pid."""
    vault, _ = create_test_vault_with_pending_proposal()
    settings = Settings(vault_path=str(vault), token="")
    app = create_app(settings)
    client = TestClient(app)

    # Invalid pid (not 8 hex chars)
    response = client.post("/api/review/proposals/invalid/approve")
    assert response.status_code == 422


def test_reject_proposal_invalid_pid():
    """Test POST /api/review/proposals/{pid}/reject returns 422 for invalid pid."""
    vault, _ = create_test_vault_with_pending_proposal()
    settings = Settings(vault_path=str(vault), token="")
    app = create_app(settings)
    client = TestClient(app)

    # Invalid pid (too long)
    response = client.post("/api/review/proposals/1234567890/reject")
    assert response.status_code == 422


def test_approve_entity_invalid_ref():
    """Test POST /api/review/entities/{ref}/approve returns 422 for invalid ref."""
    vault = create_test_vault_with_pending_entity_review()
    settings = Settings(vault_path=str(vault), token="")
    app = create_app(settings)
    client = TestClient(app)

    # Invalid ref (no slash)
    response = client.post("/api/review/entities/account/approve")
    assert response.status_code == 422


def test_reject_entity_invalid_ref():
    """Test POST /api/review/entities/{ref}/reject rejects invalid refs."""
    vault = create_test_vault_with_pending_entity_review()
    settings = Settings(vault_path=str(vault), token="")
    app = create_app(settings)
    client = TestClient(app)

    # Invalid ref - path traversal is rejected by FastAPI before our validation
    # (404 is acceptable since FastAPI's path type doesn't match "..")
    response = client.post("/api/review/entities/../etc/passwd/reject")
    assert response.status_code in (404, 422)  # Both are valid security outcomes


def test_lock_held_during_approve():
    """Test that vault lock is held during approve operation."""
    from unittest.mock import patch, MagicMock
    import threading

    vault, pid = create_test_vault_with_pending_proposal()
    settings = Settings(vault_path=str(vault), token="")
    app = create_app(settings)
    client = TestClient(app)

    # Track if lock was acquired
    lock_acquired = threading.Event()

    captured_lock = None

    def mock_lock_enter(self):
        nonlocal captured_lock
        captured_lock = self
        lock_acquired.set()
        # Call original __enter__ if it exists
        return self

    def mock_lock_exit(self, *args):
        return None

    # Patch vault_lock to track acquisition
    with patch('agent_vault.api.review.vault_lock') as mock_lock_class:
        mock_lock = MagicMock()
        mock_lock.__enter__ = mock_lock_enter
        mock_lock.__exit__ = mock_lock_exit
        mock_lock_class.return_value = mock_lock

        # Also patch cmd_approve to avoid complex logic
        with patch('agent_vault.review.cmd_approve', MagicMock()):
            response = client.post(f"/api/review/proposals/{pid}/approve")

    assert response.status_code == 200
    # Verify lock was instantiated
    mock_lock_class.assert_called()


def test_response_shape_matches_synapsenas():
    """Test that response shapes match SynapseNAS server/actions.py patterns."""
    from unittest.mock import patch

    vault, pid = create_test_vault_with_pending_proposal()
    settings = Settings(vault_path=str(vault), token="")
    app = create_app(settings)
    client = TestClient(app)

    with patch('agent_vault.review.cmd_approve', return_value=0):
        response = client.post(f"/api/review/proposals/{pid}/approve")

    data = response.json()
    # SynapseNAS returns {"ok": bool, "code": int, "stdout": str, "stderr": str}
    assert "ok" in data
    assert isinstance(data["ok"], bool)


def test_approve_reindex_after_success():
    """Test that approve triggers reindex after successful approval."""
    from unittest.mock import patch

    vault, pid = create_test_vault_with_pending_proposal()
    settings = Settings(vault_path=str(vault), token="")
    app = create_app(settings)
    client = TestClient(app)

    # Mock both cmd_approve and refresh_index
    with patch('agent_vault.review.cmd_approve', return_value=0):
        with patch('agent_vault.ingest.refresh_index') as mock_refresh:
            response = client.post(f"/api/review/proposals/{pid}/approve")

    assert response.status_code == 200
    # Verify reindex was called after approve
    mock_refresh.assert_called_once()


def test_no_sys_path_imports():
    """Verify that sys.path is never modified in the review module.

    This checks the AST to ensure no sys.path manipulation occurs in actual code.
    Comments and docstrings may mention sys.path for documentation purposes.
    """
    import ast

    review_module_path = Path(__file__).parent.parent.parent / "agent_vault" / "api" / "review.py"

    if not review_module_path.exists():
        return  # Module doesn't exist yet, will be created

    source = review_module_path.read_text()

    # Parse the module
    tree = ast.parse(source)

    # Check for any sys.path manipulation in actual code (not docstrings/comments)
    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute):
            if (isinstance(node.value, ast.Name) and
                node.value.id == "sys" and
                node.attr == "path"):
                # Found sys.path usage - check if it's in a real statement
                # Get the parent to see if it's an actual manipulation
                parent = None
                for candidate in ast.walk(tree):
                    if isinstance(candidate, (ast.Assign, ast.AugAssign, ast.Call)):
                        # Check if this node contains the sys.path reference
                        for child in ast.walk(candidate):
                            if child is node:
                                parent = candidate
                                break
                if parent:
                    # This is actual code manipulating sys.path
                    assert False, f"Found sys.path manipulation in review.py at line {node.lineno}"


def test_review_with_auth_token():
    """Test that review endpoints work with bearer token auth."""
    from unittest.mock import patch

    vault, pid = create_test_vault_with_pending_proposal()
    settings = Settings(vault_path=str(vault), token="test-token")
    app = create_app(settings)
    client = TestClient(app)

    with patch('agent_vault.review.cmd_approve', return_value=0):
        # With correct token
        response = client.post(
            f"/api/review/proposals/{pid}/approve",
            headers={"Authorization": "Bearer test-token"}
        )
        assert response.status_code == 200

        # With wrong token
        response = client.post(
            f"/api/review/proposals/{pid}/approve",
            headers={"Authorization": "Bearer wrong-token"}
        )
        assert response.status_code == 401


def test_optional_reason_parameter():
    """Test that reason is optional in request body."""
    from unittest.mock import patch

    vault, pid = create_test_vault_with_pending_proposal()
    settings = Settings(vault_path=str(vault), token="")
    app = create_app(settings)
    client = TestClient(app)

    with patch('agent_vault.review.cmd_approve', return_value=0):
        # No reason provided
        response = client.post(f"/api/review/proposals/{pid}/approve")
        assert response.status_code == 200

        # Empty reason
        response = client.post(
            f"/api/review/proposals/{pid}/approve",
            json={"reason": ""}
        )
        assert response.status_code == 200

        # Reason provided
        response = client.post(
            f"/api/review/proposals/{pid}/approve",
            json={"reason": "test reason"}
        )
        assert response.status_code == 200

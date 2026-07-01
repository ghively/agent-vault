"""Tests for history endpoints (runs and ledgers)."""

import json
import tempfile
from pathlib import Path

from fastapi.testclient import TestClient

from agent_vault.api.app import create_app
from agent_vault.api.config import Settings


def test_runs_empty():
    """Test GET /api/runs with empty vault (no runs file)."""
    with tempfile.TemporaryDirectory() as tmpdir:
        vault = Path(tmpdir)
        settings = Settings(vault_path=str(vault), token="")
        app = create_app(settings)
        client = TestClient(app)

        response = client.get("/api/runs")
        assert response.status_code == 200
        assert response.json() == {"runs": []}


def test_runs_with_data():
    """Test GET /api/runs returns run entries in correct order (newest first)."""
    with tempfile.TemporaryDirectory() as tmpdir:
        vault = Path(tmpdir)
        discovery = vault / "discovery"
        discovery.mkdir()

        # Write a few run entries (oldest first in file)
        runs_file = discovery / "_runs.jsonl"
        runs = [
            {
                "cadence": "daily",
                "ts": "2026-06-28T10:00:00Z",
                "rc": 0,
                "duration_s": 5,
                "detail": "ok",
            },
            {
                "cadence": "weekly",
                "ts": "2026-06-29T12:00:00Z",
                "rc": 0,
                "duration_s": 120,
                "detail": "ok",
            },
            {
                "cadence": "monthly",
                "ts": "2026-06-30T14:00:00Z",
                "rc": 1,
                "duration_s": 30,
                "detail": "validate(rc=1)",
            },
        ]

        content = ""
        for run in runs:
            content += json.dumps(run) + "\n"
        runs_file.write_text(content)

        settings = Settings(vault_path=str(vault), token="")
        app = create_app(settings)
        client = TestClient(app)

        response = client.get("/api/runs")
        assert response.status_code == 200
        data = response.json()
        assert "runs" in data
        assert len(data["runs"]) == 3

        # Should be newest first (reversed order)
        assert data["runs"][0]["cadence"] == "monthly"
        assert data["runs"][0]["ts"] == "2026-06-30T14:00:00Z"
        assert data["runs"][1]["cadence"] == "weekly"
        assert data["runs"][2]["cadence"] == "daily"


def test_runs_limit():
    """Test GET /api/runs with limit parameter."""
    with tempfile.TemporaryDirectory() as tmpdir:
        vault = Path(tmpdir)
        discovery = vault / "discovery"
        discovery.mkdir()

        # Write 10 run entries
        runs_file = discovery / "_runs.jsonl"
        content = ""
        for i in range(10):
            run = {
                "cadence": "daily",
                "ts": f"2026-06-{i+20:02d}T10:00:00Z",
                "rc": 0,
                "duration_s": 5,
                "detail": "ok",
            }
            content += json.dumps(run) + "\n"
        runs_file.write_text(content)

        settings = Settings(vault_path=str(vault), token="")
        app = create_app(settings)
        client = TestClient(app)

        # Test with limit=5
        response = client.get("/api/runs?limit=5")
        assert response.status_code == 200
        data = response.json()
        assert len(data["runs"]) == 5


def test_ledgers_empty():
    """Test GET /api/ledgers with empty vault (no ledger files)."""
    with tempfile.TemporaryDirectory() as tmpdir:
        vault = Path(tmpdir)
        settings = Settings(vault_path=str(vault), token="")
        app = create_app(settings)
        client = TestClient(app)

        response = client.get("/api/ledgers")
        assert response.status_code == 200
        assert response.json() == {"proposals": 0, "promoted": 0, "manifest": 0}


def test_ledgers_with_data():
    """Test GET /api/ledgers returns correct counts for all ledgers."""
    with tempfile.TemporaryDirectory() as tmpdir:
        vault = Path(tmpdir)
        discovery = vault / "discovery"
        discovery.mkdir()
        raw = vault / "raw"
        raw.mkdir()

        # Write proposals
        proposals_file = discovery / "proposals.jsonl"
        content = ""
        for i in range(3):
            proposal = {"ts": "2026-06-30T10:00:00Z", "proposal": f"item-{i}"}
            content += json.dumps(proposal) + "\n"
        proposals_file.write_text(content)

        # Write promoted entries
        promoted_file = discovery / "promoted.jsonl"
        content = ""
        for i in range(5):
            promoted = {"ts": "2026-06-30T11:00:00Z", "action": "queue_for_review"}
            content += json.dumps(promoted) + "\n"
        promoted_file.write_text(content)

        # Write manifest entries
        manifest_file = raw / "_manifest.jsonl"
        content = ""
        for i in range(7):
            entry = {"path": f"entity-{i}.md", "hash": f"hash-{i}"}
            content += json.dumps(entry) + "\n"
        manifest_file.write_text(content)

        settings = Settings(vault_path=str(vault), token="")
        app = create_app(settings)
        client = TestClient(app)

        response = client.get("/api/ledgers")
        assert response.status_code == 200
        data = response.json()
        assert data["proposals"] == 3
        assert data["promoted"] == 5
        assert data["manifest"] == 7


def test_runs_auth_required_when_token_set():
    """Test GET /api/runs requires auth when token is set."""
    with tempfile.TemporaryDirectory() as tmpdir:
        vault = Path(tmpdir)
        settings = Settings(vault_path=str(vault), token="test-token")
        app = create_app(settings)
        client = TestClient(app)

        # No auth header
        response = client.get("/api/runs")
        assert response.status_code == 401

        # Wrong token
        response = client.get(
            "/api/runs", headers={"Authorization": "Bearer wrong-token"}
        )
        assert response.status_code == 401

        # Correct token
        response = client.get(
            "/api/runs", headers={"Authorization": "Bearer test-token"}
        )
        assert response.status_code == 200


def test_ledgers_auth_required_when_token_set():
    """Test GET /api/ledgers requires auth when token is set."""
    with tempfile.TemporaryDirectory() as tmpdir:
        vault = Path(tmpdir)
        settings = Settings(vault_path=str(vault), token="test-token")
        app = create_app(settings)
        client = TestClient(app)

        # No auth header
        response = client.get("/api/ledgers")
        assert response.status_code == 401

        # Correct token
        response = client.get(
            "/api/ledgers", headers={"Authorization": "Bearer test-token"}
        )
        assert response.status_code == 200

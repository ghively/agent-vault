"""Tests for GET /api/status (dashboard snapshot)."""

import datetime
import json
import tempfile
from pathlib import Path

from fastapi.testclient import TestClient

from agent_vault.api.app import create_app
from agent_vault.api.config import Settings


def _date(days_from_now: int) -> str:
    return (datetime.date.today() + datetime.timedelta(days=days_from_now)).isoformat()


def create_test_vault() -> Path:
    """Create a minimal test vault with an index, dates, and run history."""
    vault_dir = tempfile.mkdtemp()
    vault = Path(vault_dir)
    (vault / "discovery").mkdir(parents=True)

    index = {
        "count": 4,
        "entities": [
            {
                "slug": "bofa-checking",
                "type": "account",
                "subtype": "checking",
                "title": "Bank of America Checking",
                "status": "compiled",
                "tags": ["banking"],
                "path": "entities/account/bofa-checking.md",
                "dates": {"due": _date(5)},
            },
            {
                "slug": "passport-jane",
                "type": "document",
                "subtype": "passport",
                "title": "Jane Passport",
                "status": "compiled",
                "tags": ["identity"],
                "path": "entities/document/passport-jane.md",
                "dates": {"expires": _date(30)},
            },
            {
                "slug": "domain-example",
                "type": "service",
                "subtype": "domain",
                "title": "example.com Domain",
                "status": "needs-review",
                "tags": [],
                "path": "entities/service/domain-example.md",
                "dates": {"renews": _date(10)},
            },
            {
                "slug": "old-widget",
                "type": "asset",
                "subtype": "gadget",
                "title": "Old Widget",
                "status": "stub",
                "tags": [],
                "path": "entities/asset/old-widget.md",
                # expires far in the future — outside the 90-day window
                "dates": {"expires": _date(400)},
            },
        ],
    }
    (vault / "_index.json").write_text(json.dumps(index), encoding="utf-8")

    run_line = {
        "cadence": "daily",
        "ts": "2026-07-01T02:00:00Z",
        "rc": 0,
        "duration_s": 12,
        "detail": "ingest: 3 new",
    }
    (vault / "discovery" / "_runs.jsonl").write_text(
        json.dumps({"cadence": "weekly", "ts": "2026-06-28T02:00:00Z", "rc": 1,
                    "duration_s": 30, "detail": "compile failed"})
        + "\n"
        + json.dumps(run_line)
        + "\n",
        encoding="utf-8",
    )
    return vault


def test_status_happy_path():
    """Status aggregates counts, due/expiring windows, breakdown, last run."""
    vault = create_test_vault()
    settings = Settings(vault_path=str(vault), token="")
    app = create_app(settings)
    client = TestClient(app)

    response = client.get("/api/status")
    assert response.status_code == 200
    data = response.json()

    assert data["counts"] == {"total": 4, "compiled": 2, "needs_review": 1}

    # due: one item within 30 days
    assert len(data["due"]) == 1
    due = data["due"][0]
    assert due["slug"] == "bofa-checking"
    assert due["title"] == "Bank of America Checking"
    assert due["days"] == 5
    assert due["date"] == _date(5)

    # expiring: renews@10d and expires@30d within 90 days, sorted by days;
    # the 400-day one is outside the window
    assert [(e["slug"], e["field"]) for e in data["expiring"]] == [
        ("domain-example", "renews"),
        ("passport-jane", "expires"),
    ]

    assert data["breakdown"] == {"account": 1, "document": 1, "service": 1, "asset": 1}

    # last_run is the LAST line of _runs.jsonl
    assert data["last_run"] == {
        "cadence": "daily",
        "ts": "2026-07-01T02:00:00Z",
        "rc": 0,
        "duration_s": 12,
        "detail": "ingest: 3 new",
    }


def test_status_empty_vault():
    """Status degrades gracefully with no index and no run history."""
    vault = Path(tempfile.mkdtemp())
    settings = Settings(vault_path=str(vault), token="")
    app = create_app(settings)
    client = TestClient(app)

    response = client.get("/api/status")
    assert response.status_code == 200
    data = response.json()
    assert data["counts"] == {"total": 0, "compiled": 0, "needs_review": 0}
    assert data["due"] == []
    assert data["expiring"] == []
    assert data["breakdown"] == {}
    assert data["last_run"] is None


def test_status_requires_auth_when_token_set():
    """Status is behind the bearer token like the other /api reads."""
    vault = create_test_vault()
    settings = Settings(vault_path=str(vault), token="secret")
    app = create_app(settings)
    client = TestClient(app)

    assert client.get("/api/status").status_code == 401
    ok = client.get("/api/status", headers={"Authorization": "Bearer secret"})
    assert ok.status_code == 200

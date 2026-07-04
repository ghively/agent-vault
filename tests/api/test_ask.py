"""Tests for GET /api/ask (deterministic query routing)."""

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
    """Create a minimal test vault with an index covering all three intents."""
    vault_dir = tempfile.mkdtemp()
    vault = Path(vault_dir)

    index = {
        "count": 3,
        "entities": [
            {
                "slug": "water-bill",
                "type": "account",
                "subtype": "utility",
                "title": "City Water Bill",
                "status": "compiled",
                "tags": ["utilities"],
                "path": "entities/account/water-bill.md",
                "dates": {"due": _date(3)},
                "_match": ["water-bill", "city water bill"],
            },
            {
                "slug": "passport-jane",
                "type": "document",
                "subtype": "passport",
                "title": "Jane Passport",
                "status": "compiled",
                "tags": ["identity"],
                "path": "entities/document/passport-jane.md",
                "dates": {"expires": _date(45)},
                "_match": ["passport-jane", "jane passport"],
            },
            {
                "slug": "honda-civic",
                "type": "asset",
                "subtype": "vehicle",
                "title": "Honda Civic",
                "status": "compiled",
                "tags": ["car"],
                "path": "entities/asset/honda-civic.md",
                "_match": ["honda-civic", "honda civic"],
            },
        ],
    }
    (vault / "_index.json").write_text(json.dumps(index), encoding="utf-8")
    return vault


def _client() -> TestClient:
    vault = create_test_vault()
    settings = Settings(vault_path=str(vault), token="")
    return TestClient(create_app(settings))


def test_ask_due_intent():
    """Queries containing 'due' route to due items."""
    client = _client()
    response = client.get("/api/ask", params={"q": "what bills are due soon"})
    assert response.status_code == 200
    data = response.json()
    assert data["intent"] == "due"
    assert len(data["items"]) == 1
    item = data["items"][0]
    assert item["slug"] == "water-bill"
    assert item["date"] == _date(3)
    assert item["days"] == 3


def test_ask_expiring_intent():
    """Queries containing 'expir' or 'renew' route to expiring items."""
    client = _client()
    for q in ("anything expiring?", "what renews this quarter"):
        response = client.get("/api/ask", params={"q": q})
        assert response.status_code == 200
        data = response.json()
        assert data["intent"] == "expiring"
        assert [i["slug"] for i in data["items"]] == ["passport-jane"]
        assert data["items"][0]["field"] == "expires"


def test_ask_find_intent():
    """Any other query is a substring find over the index."""
    client = _client()
    response = client.get("/api/ask", params={"q": "honda"})
    assert response.status_code == 200
    data = response.json()
    assert data["intent"] == "find"
    assert len(data["items"]) == 1
    item = data["items"][0]
    assert item["slug"] == "honda-civic"
    assert item["title"] == "Honda Civic"
    assert item["type"] == "asset"


def test_ask_find_no_match():
    client = _client()
    response = client.get("/api/ask", params={"q": "zzz-nothing"})
    assert response.status_code == 200
    assert response.json() == {"intent": "find", "items": []}


def test_ask_missing_q_is_422():
    """q is required — omitting it is a validation error."""
    client = _client()
    assert client.get("/api/ask").status_code == 422


def test_ask_empty_q_is_empty_find():
    """Empty q returns an empty find result (never a full dump)."""
    client = _client()
    response = client.get("/api/ask", params={"q": ""})
    assert response.status_code == 200
    assert response.json() == {"intent": "find", "items": []}

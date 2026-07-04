"""Tests for edit endpoints (schema, reclassify, PATCH frontmatter, raw editor)."""

import json
import tempfile
from pathlib import Path

from fastapi.testclient import TestClient

from agent_vault.api.app import create_app
from agent_vault.api.config import Settings

SCHEMA_YAML = """version: 1
types:
  asset:
    subtypes: [appliance, electronics]
  vehicle:
    subtypes: [car, truck]
  document:
    subtypes: [receipt, warranty]
  unknown:
tags:
  banking: { promoted: 2026-05-21, count: 1 }
  primary: {}
  vehicle: {}
"""

HONDA_MD = """---
slug: honda-crv
type: asset
subtype: electronics
title: 2019 Honda CR-V
status: compiled
confidence: 0.95
created: 2026-01-15
sources: []
sources_hash: seed000001
tags: [primary]
related:
  - document/crv-receipt
---

<!-- LINKS:BEGIN -->
- Related: [[document/crv-receipt]]
<!-- LINKS:END -->

Family vehicle, misfiled as an asset.
"""

RECEIPT_MD = """---
slug: crv-receipt
type: document
subtype: receipt
title: CR-V Purchase Receipt
status: compiled
confidence: 0.9
created: 2026-01-15
sources: []
sources_hash: seed000002
related:
  - asset/honda-crv
---

<!-- LINKS:BEGIN -->
- Related: [[asset/honda-crv]]
<!-- LINKS:END -->

Receipt for the CR-V purchase.
"""

TOASTER_MD = """---
slug: toaster
type: asset
subtype: appliance
title: Old Toaster
status: compiled
confidence: 0.9
created: 2026-02-01
sources: []
sources_hash: seed000003
tags: [primary]
---

<!-- LINKS:BEGIN -->
<!-- LINKS:END -->

A toaster we own.
"""

# Already-invalid entity (status stub + non-empty prose) — any PATCH must
# fail post-write validation and roll back.
BROKEN_STUB_MD = """---
slug: broken-stub
type: asset
subtype: appliance
title: Broken Stub
status: stub
confidence: 0.5
created: 2026-02-01
sources: []
sources_hash: seed000004
---

<!-- LINKS:BEGIN -->
<!-- LINKS:END -->

Stubs must not carry prose, so this file is invalid.
"""

GITHUB_TOKEN = "ghp_" + "a" * 36  # matches secret_scan's strict github_token shape


def create_edit_vault() -> Path:
    """Minimal vault: schema with 4 types (one empty-bodied), 3 tags, 4 entities."""
    vault = Path(tempfile.mkdtemp())
    for t in ("asset", "vehicle", "document"):
        (vault / "entities" / t).mkdir(parents=True)
    (vault / "discovery").mkdir(parents=True)
    (vault / "registry").mkdir(parents=True)
    (vault / "registry" / "schema.yaml").write_text(SCHEMA_YAML, encoding="utf-8")
    (vault / "entities" / "asset" / "honda-crv.md").write_text(HONDA_MD, encoding="utf-8")
    (vault / "entities" / "document" / "crv-receipt.md").write_text(RECEIPT_MD, encoding="utf-8")
    (vault / "entities" / "asset" / "toaster.md").write_text(TOASTER_MD, encoding="utf-8")
    (vault / "entities" / "asset" / "broken-stub.md").write_text(BROKEN_STUB_MD, encoding="utf-8")
    return vault


def make_client(vault: Path, token: str = "") -> TestClient:
    return TestClient(create_app(Settings(vault_path=str(vault), token=token)))


def read_ledger(vault: Path) -> list[dict]:
    p = vault / "discovery" / "promoted.jsonl"
    if not p.exists():
        return []
    return [json.loads(line) for line in p.read_text(encoding="utf-8").splitlines() if line]


# ============================================================================
# GET /api/schema
# ============================================================================
def test_schema_shape():
    client = make_client(create_edit_vault())
    r = client.get("/api/schema")
    assert r.status_code == 200
    data = r.json()
    # Sorted types, each with sorted subtypes; empty-bodied type -> []
    assert [t["type"] for t in data["types"]] == ["asset", "document", "unknown", "vehicle"]
    by_type = {t["type"]: t["subtypes"] for t in data["types"]}
    assert by_type["asset"] == ["appliance", "electronics"]
    assert by_type["vehicle"] == ["car", "truck"]
    assert by_type["unknown"] == []  # empty/None body tolerated
    assert data["tags"] == ["banking", "primary", "vehicle"]


# ============================================================================
# POST /api/entities/{slug}/reclassify
# ============================================================================
def test_reclassify_happy_path():
    vault = create_edit_vault()
    client = make_client(vault)

    r = client.post(
        "/api/entities/honda-crv/reclassify",
        json={"to_type": "vehicle", "to_subtype": "car", "reason": "it drives"},
    )
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["slug"] == "honda-crv"
    assert data["from"] == "asset/electronics"
    assert data["to"] == "vehicle/car"
    assert data["moved"] is True
    assert data["refs_rewritten"] == 1
    assert isinstance(data["detail"], str) and data["detail"]

    # File actually moved across type dirs, frontmatter rewritten.
    assert not (vault / "entities" / "asset" / "honda-crv.md").exists()
    moved = vault / "entities" / "vehicle" / "honda-crv.md"
    assert moved.exists()
    text = moved.read_text(encoding="utf-8")
    assert "type: vehicle" in text
    assert "subtype: car" in text

    # Cross-ref in the OTHER entity rewritten (frontmatter + links block).
    receipt = (vault / "entities" / "document" / "crv-receipt.md").read_text(encoding="utf-8")
    assert "vehicle/honda-crv" in receipt
    assert "asset/honda-crv" not in receipt

    # Ledger: human-provenance queue record, approval, and the apply records.
    recs = [r_ for r_ in read_ledger(vault)
            if r_.get("identity") == ["reclassify", "honda-crv", "vehicle", "car"]]
    actions = [r_["action"] for r_ in recs]
    assert actions == ["queue_for_review", "review_approved",
                       "reclassify_started", "reclassify_applied"]
    queue_rec = recs[0]
    assert queue_rec["kind"] == "reclassify"
    assert queue_rec["origin"] == "human_review"
    assert queue_rec["proposal"]["proposed_by"] == "human_review"
    assert queue_rec["proposal"]["from_entity"] == "honda-crv"
    assert queue_rec["proposal"]["to_type"] == "vehicle"
    assert queue_rec["proposal"]["to_subtype"] == "car"
    assert queue_rec["reason"] == "it drives"

    # Index rebuilt by apply_all: subsequent GET sees the new classification.
    g = client.get("/api/entities/honda-crv")
    assert g.status_code == 200
    assert g.json()["type"] == "vehicle"
    assert g.json()["subtype"] == "car"


def test_reclassify_invalid_target_400():
    client = make_client(create_edit_vault())
    r = client.post(
        "/api/entities/honda-crv/reclassify",
        json={"to_type": "spaceship", "to_subtype": "shuttle"},
    )
    assert r.status_code == 400
    assert "spaceship" in r.json()["detail"]

    r = client.post(
        "/api/entities/honda-crv/reclassify",
        json={"to_type": "vehicle", "to_subtype": "hovercraft"},
    )
    assert r.status_code == 400
    assert "hovercraft" in r.json()["detail"]


def test_reclassify_same_classification_400():
    vault = create_edit_vault()
    client = make_client(vault)
    r = client.post(
        "/api/entities/toaster/reclassify",
        json={"to_type": "asset", "to_subtype": "appliance"},
    )
    assert r.status_code == 400
    assert "already classified" in r.json()["detail"]
    # Nothing appended to the ledger, nothing moved.
    assert read_ledger(vault) == []
    assert (vault / "entities" / "asset" / "toaster.md").exists()


def test_reclassify_unknown_slug_404():
    client = make_client(create_edit_vault())
    r = client.post(
        "/api/entities/no-such-entity/reclassify",
        json={"to_type": "vehicle", "to_subtype": "car"},
    )
    assert r.status_code == 404


# ============================================================================
# PATCH /api/entities/{slug}
# ============================================================================
def test_patch_happy_path_minimal_diff():
    vault = create_edit_vault()
    client = make_client(vault)
    path = vault / "entities" / "asset" / "toaster.md"
    original = path.read_text(encoding="utf-8")

    r = client.patch(
        "/api/entities/toaster",
        json={"title": "Shiny Toaster", "tags": ["banking"], "notes": "kitchen appliance"},
    )
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["title"] == "Shiny Toaster"
    assert data["slug"] == "toaster"
    assert data["type"] == "asset"
    assert data["prose"] == "A toaster we own."

    # Byte-exact minimal diff: only the title/tags lines change, notes is
    # appended as the last frontmatter line; everything else is untouched.
    expected = original.replace(
        "title: Old Toaster", "title: Shiny Toaster"
    ).replace(
        "tags: [primary]\n---", "tags: [banking]\nnotes: kitchen appliance\n---"
    )
    assert path.read_text(encoding="utf-8") == expected

    # Response matches a subsequent GET (index was refreshed).
    g = client.get("/api/entities/toaster")
    assert g.status_code == 200
    assert g.json() == data


def test_patch_empty_body_and_unknown_keys_400():
    client = make_client(create_edit_vault())
    r = client.patch("/api/entities/toaster", json={})
    assert r.status_code == 400

    r = client.patch("/api/entities/toaster", json={"status": "archived"})
    assert r.status_code == 400
    assert "status" in r.json()["detail"]


def test_patch_title_rules_400():
    client = make_client(create_edit_vault())
    assert client.patch("/api/entities/toaster", json={"title": ""}).status_code == 400
    assert client.patch("/api/entities/toaster", json={"title": "a\nb"}).status_code == 400
    assert client.patch("/api/entities/toaster", json={"title": "x" * 301}).status_code == 400


def test_patch_unknown_tag_400_lists_valid_tags():
    vault = create_edit_vault()
    client = make_client(vault)
    original = (vault / "entities" / "asset" / "toaster.md").read_text(encoding="utf-8")
    r = client.patch("/api/entities/toaster", json={"tags": ["banking", "nonsense"]})
    assert r.status_code == 400
    detail = r.json()["detail"]
    assert "nonsense" in detail
    for valid in ("banking", "primary", "vehicle"):
        assert valid in detail
    assert (vault / "entities" / "asset" / "toaster.md").read_text(encoding="utf-8") == original


def test_patch_secret_value_422_nothing_written():
    vault = create_edit_vault()
    client = make_client(vault)
    path = vault / "entities" / "asset" / "toaster.md"
    original = path.read_text(encoding="utf-8")
    r = client.patch("/api/entities/toaster", json={"notes": f"token is {GITHUB_TOKEN}"})
    assert r.status_code == 422
    assert "secret" in r.json()["detail"]
    assert path.read_text(encoding="utf-8") == original


def test_patch_labeled_secret_422_nothing_written():
    """B4: the human-edit path also catches a LABELED plaintext secret
    (`password: <value>`) that the strict branded-format gate would miss."""
    vault = create_edit_vault()
    client = make_client(vault)
    path = vault / "entities" / "asset" / "toaster.md"
    original = path.read_text(encoding="utf-8")
    r = client.patch("/api/entities/toaster", json={"notes": "password: Hunter2Zzz!"})
    assert r.status_code == 422
    assert "secret" in r.json()["detail"]
    assert path.read_text(encoding="utf-8") == original


def test_patch_validate_failure_rolls_back():
    vault = create_edit_vault()
    client = make_client(vault)
    path = vault / "entities" / "asset" / "broken-stub.md"
    original = path.read_text(encoding="utf-8")
    # The entity is already invalid (stub with prose): the post-write validate
    # must fail and restore the original bytes.
    r = client.patch("/api/entities/broken-stub", json={"title": "Renamed Stub"})
    assert r.status_code == 422
    detail = r.json()["detail"]
    assert "rolled back" in detail["message"]
    assert any("stub" in e for e in detail["errors"])
    assert path.read_text(encoding="utf-8") == original


def test_patch_unknown_slug_404():
    client = make_client(create_edit_vault())
    assert client.patch("/api/entities/nope", json={"title": "X"}).status_code == 404


# ============================================================================
# GET/PUT /api/entities/{slug}/raw
# ============================================================================
def test_raw_get_put_round_trip():
    vault = create_edit_vault()
    client = make_client(vault)

    g = client.get("/api/entities/toaster/raw")
    assert g.status_code == 200
    data = g.json()
    assert data["path"] == "entities/asset/toaster.md"
    assert data["content"] == TOASTER_MD

    new_content = TOASTER_MD.replace("A toaster we own.", "A toaster we love.")
    p = client.put("/api/entities/toaster/raw", json={"content": new_content})
    assert p.status_code == 200, p.text
    assert p.json() == {"ok": True, "path": "entities/asset/toaster.md"}

    g2 = client.get("/api/entities/toaster/raw")
    assert g2.json()["content"] == new_content
    assert (vault / "entities" / "asset" / "toaster.md").read_text(
        encoding="utf-8") == new_content


def test_raw_put_slug_and_type_mismatch_400():
    vault = create_edit_vault()
    client = make_client(vault)
    original = (vault / "entities" / "asset" / "toaster.md").read_text(encoding="utf-8")

    r = client.put(
        "/api/entities/toaster/raw",
        json={"content": TOASTER_MD.replace("slug: toaster", "slug: oven")},
    )
    assert r.status_code == 400
    assert "slug" in r.json()["detail"]

    r = client.put(
        "/api/entities/toaster/raw",
        json={"content": TOASTER_MD.replace("type: asset", "type: document")},
    )
    assert r.status_code == 400
    assert "reclassify" in r.json()["detail"]

    assert (vault / "entities" / "asset" / "toaster.md").read_text(encoding="utf-8") == original


def test_raw_put_secret_in_frontmatter_422_nothing_written():
    vault = create_edit_vault()
    client = make_client(vault)
    path = vault / "entities" / "asset" / "toaster.md"
    original = path.read_text(encoding="utf-8")
    bad = TOASTER_MD.replace("tags: [primary]", f"tags: [primary]\nserial: {GITHUB_TOKEN}")
    r = client.put("/api/entities/toaster/raw", json={"content": bad})
    assert r.status_code == 422
    assert "secret" in r.json()["detail"]
    assert path.read_text(encoding="utf-8") == original


def test_raw_put_validate_failure_rolls_back():
    vault = create_edit_vault()
    client = make_client(vault)
    path = vault / "entities" / "asset" / "toaster.md"
    original = path.read_text(encoding="utf-8")
    # Passes the shape/slug/type/secret pre-checks but fails validate_file
    # (LINKS block removed) -> written then rolled back.
    bad = TOASTER_MD.replace("<!-- LINKS:BEGIN -->\n<!-- LINKS:END -->\n", "")
    r = client.put("/api/entities/toaster/raw", json={"content": bad})
    assert r.status_code == 422
    detail = r.json()["detail"]
    assert any("LINKS" in e for e in detail["errors"])
    assert path.read_text(encoding="utf-8") == original


def test_raw_put_malformed_shape_400():
    vault = create_edit_vault()
    client = make_client(vault)

    # No frontmatter block at all
    r = client.put("/api/entities/toaster/raw", json={"content": "just some text"})
    assert r.status_code == 400

    # Empty content
    r = client.put("/api/entities/toaster/raw", json={"content": "   "})
    assert r.status_code == 400

    # Over the 1MB cap
    huge = TOASTER_MD + ("x" * (1024 * 1024))
    r = client.put("/api/entities/toaster/raw", json={"content": huge})
    assert r.status_code == 400

    # File untouched throughout
    assert (vault / "entities" / "asset" / "toaster.md").read_text(
        encoding="utf-8") == TOASTER_MD


def test_raw_unknown_slug_404():
    client = make_client(create_edit_vault())
    assert client.get("/api/entities/nope/raw").status_code == 404
    assert client.put("/api/entities/nope/raw", json={"content": TOASTER_MD}).status_code == 404


# ============================================================================
# Auth gating (same posture as the other /api routes)
# ============================================================================
def test_edit_endpoints_require_token_when_auth_enabled():
    vault = create_edit_vault()
    client = make_client(vault, token="test-token")

    assert client.get("/api/schema").status_code == 401
    assert client.post(
        "/api/entities/honda-crv/reclassify",
        json={"to_type": "vehicle", "to_subtype": "car"},
    ).status_code == 401
    assert client.patch("/api/entities/toaster", json={"title": "X"}).status_code == 401
    assert client.get("/api/entities/toaster/raw").status_code == 401
    assert client.put(
        "/api/entities/toaster/raw", json={"content": TOASTER_MD}
    ).status_code == 401

    # And with the right bearer, requests go through.
    ok = client.get("/api/schema", headers={"Authorization": "Bearer test-token"})
    assert ok.status_code == 200

"""promote.py's registry writers (apply_biller/apply_shape) and the decide()
gates that guard them: hostile scalars must be quoted so patterns.yaml stays
valid YAML, malformed ids/tags must never auto-apply, and a human rejection
must survive `compact --apply` (which shrinks raw sighting counts but
preserves distinct from-entities)."""
import json
import shutil
from pathlib import Path

import yaml

from agent_vault import compact, promote, review

HERE = Path(__file__).resolve().parent
VAULT_SRC = HERE.parent

EMPTY_RS = {
    "tags": set(),
    "aliases": set(),
    "subtypes_by_type": {},
    "types": set(),
    "entity_slugs": set(),
    "biller_ids": set(),
    "shape_ids": set(),
    "field_mapping_ids": set(),
}


def _rs(**kw):
    rs = dict(EMPTY_RS)
    rs.update(kw)
    return rs


def _agg(kind, proposal, sightings=1, from_entities=None, max_conf=1.0):
    return {
        "kind": kind,
        "proposal": proposal,
        "sightings": sightings,
        "from_entities": [] if from_entities is None else from_entities,
        "max_confidence": max_conf,
        "identity": (kind,),
    }


def _sandbox(tmp_path):
    dst = tmp_path / "vault"
    shutil.copytree(VAULT_SRC, dst, ignore=shutil.ignore_patterns("tests", "__pycache__"))
    return dst


BILLER_RULE = {"new_biller": {"auto": True, "min_sightings": 1}}
SHAPE_RULE = {"new_shape": {"auto": True, "min_sightings": 1}}


# ---------------------------------------------------------------------------
# decide() gates — malformed ids/slugs/tags are never auto-applied
# ---------------------------------------------------------------------------

def test_decide_biller_rejects_invalid_id():
    agg = _agg("biller", {"kind": "biller", "id": "acme!!", "matches": ["acme"],
                          "confidence": 0.9},
               sightings=2, from_entities=["a", "b"])
    d = promote.decide(agg, BILLER_RULE, {}, _rs())
    assert d["action"] == "queue_for_review"
    assert "slug" in d["reason"]


def test_decide_biller_rejects_invalid_vendor_slug():
    agg = _agg("biller", {"kind": "biller", "id": "acme", "matches": ["acme"],
                          "vendor_slug": "Bad Slug!", "confidence": 0.9},
               sightings=2, from_entities=["a", "b"])
    d = promote.decide(agg, BILLER_RULE, {}, _rs())
    assert d["action"] == "queue_for_review"
    assert "vendor_slug" in d["reason"]


def test_decide_biller_rejects_unknown_default_tag():
    agg = _agg("biller", {"kind": "biller", "id": "acme", "matches": ["acme"],
                          "default_tags": ["not-a-real-tag"], "confidence": 0.9},
               sightings=2, from_entities=["a", "b"])
    d = promote.decide(agg, BILLER_RULE, {}, _rs(tags={"banking"}))
    assert d["action"] == "queue_for_review"
    assert "default_tag" in d["reason"]


def test_decide_biller_accepts_known_default_tag():
    agg = _agg("biller", {"kind": "biller", "id": "acme", "matches": ["acme"],
                          "default_tags": ["banking"], "confidence": 0.9},
               sightings=2, from_entities=["a", "b"])
    d = promote.decide(agg, BILLER_RULE, {}, _rs(tags={"banking"}))
    assert d["action"] == "auto_promote"


def test_decide_shape_rejects_invalid_id():
    agg = _agg("shape", {"kind": "shape", "id": "shape/with/slashes",
                         "type": "document", "subtype": "statement",
                         "keywords": ["x"], "confidence": 0.9},
               sightings=2, from_entities=["a", "b"])
    d = promote.decide(agg, SHAPE_RULE, {},
                       _rs(types={"document"},
                           subtypes_by_type={"document": ["statement"]}))
    assert d["action"] == "queue_for_review"
    assert "slug" in d["reason"]


def test_decide_unknown_confidence_band_fails_loud():
    agg = _agg("tag", {"kind": "tag", "name": "gadget"},
               sightings=5, from_entities=["a", "b", "c"], max_conf=1.0)
    rule = {"new_tag": {"auto": True, "min_sightings": 1,
                        "require_confidence": "extreme"}}
    d = promote.decide(agg, rule, {}, _rs())
    assert d["action"] == "queue_for_review"
    assert "extreme" in d["reason"]


def test_decide_accepts_med_alias_for_medium():
    agg = _agg("tag", {"kind": "tag", "name": "gadget"},
               sightings=5, from_entities=["a", "b", "c"], max_conf=0.7)
    rule = {"new_tag": {"auto": True, "min_sightings": 1,
                        "require_confidence": "med"}}
    d = promote.decide(agg, rule, {}, _rs())
    assert d["action"] == "auto_promote"


# ---------------------------------------------------------------------------
# apply_biller / apply_shape — hostile scalars must still yield valid YAML
# ---------------------------------------------------------------------------

def test_apply_biller_hostile_values_roundtrip(tmp_path):
    vault = _sandbox(tmp_path)
    agg = {"proposal": {
        "matches": ["acme: the vendor", "[weird]", "1984"],
        "vendor_slug": "no",                     # YAML would read bool False
        "default_tags": ["1984", "banking"],     # YAML would read int
        "confidence": 0.9,
    }, "sightings": 2}
    assert promote.apply_biller(str(vault), "0234", agg, "2026-07-04")
    doc = yaml.safe_load((vault / "registry" / "patterns.yaml").read_text(encoding="utf-8"))
    entries = [b for b in doc["billers"] if b.get("id") == "0234"]
    assert entries, "id must round-trip as the string '0234', not octal 156"
    b = entries[0]
    assert b["vendor_slug"] == "no"
    assert b["default_tags"] == ["1984", "banking"]
    assert "acme: the vendor" in b["matches"]
    assert "1984" in b["matches"]


def test_apply_shape_hostile_values_roundtrip(tmp_path):
    vault = _sandbox(tmp_path)
    agg = {"proposal": {
        "type": "document",
        "subtype": "statement",
        "keywords": ["total: due", "#anchor"],
        "min_matches": 1,
        "applies_to": ["pdf", "0234"],
        "confidence": 0.85,
    }, "sightings": 2}
    assert promote.apply_shape(str(vault), "no", agg, "2026-07-04")
    doc = yaml.safe_load((vault / "registry" / "patterns.yaml").read_text(encoding="utf-8"))
    entries = [s for s in doc["shapes"] if s.get("id") == "no"]
    assert entries, "id must round-trip as the string 'no', not False"
    s = entries[0]
    assert s["type"] == "document"
    assert s["applies_to"] == ["pdf", "0234"]
    assert "total: due" in s["keywords"]
    assert "#anchor" in s["keywords"]


def test_apply_biller_is_idempotent(tmp_path):
    vault = _sandbox(tmp_path)
    agg = {"proposal": {"matches": ["acme"], "confidence": 0.9}, "sightings": 2}
    assert promote.apply_biller(str(vault), "acme", agg, "2026-07-04")
    assert not promote.apply_biller(str(vault), "acme", agg, "2026-07-04")


# ---------------------------------------------------------------------------
# compact -> promote interplay: a human rejection stands after compaction
# ---------------------------------------------------------------------------

def _write_proposals(vault, records):
    p = vault / "discovery" / "proposals.jsonl"
    p.parent.mkdir(exist_ok=True)
    p.write_text("\n".join(json.dumps(r) for r in records) + "\n", encoding="utf-8")


def test_rejection_stands_after_compaction(tmp_path):
    vault = _sandbox(tmp_path)
    ident = ("type", "gizmo")
    # 5 raw records but only 2 DISTINCT source entities (recompiles duplicate
    # proposals; compact --apply collapses them back to 2 records).
    recs = []
    for i, fe in enumerate(["e1", "e1", "e1", "e2", "e2"]):
        recs.append({"ts": f"2026-07-0{i + 1}T00:00:00+00:00", "from_entity": fe,
                     "from_sources": [], "model": "test", "contract_version": "2.0",
                     "proposal": {"kind": "type", "name": "gizmo",
                                  "evidence": "x", "confidence": 0.9}})
    _write_proposals(vault, recs)

    s1 = promote.promote(str(vault))
    assert any(tuple(r["identity"]) == ident for r in s1["queued"])

    # Human rejects it.
    pend = review.pending_proposals(str(vault))
    sids = [sid for sid, i, _ in pend if tuple(i) == ident]
    assert sids, "the queued proposal must be visible to review"
    review.cmd_reject(str(vault), sids[0], reason="not wanted")

    # Rejection stands on an ordinary re-run (no new evidence).
    s2 = promote.promote(str(vault))
    assert not any(tuple(r["identity"]) == ident for r in s2["queued"])

    # Compact the logs: raw proposal records shrink 5 -> 2.
    results = compact.compact_vault(str(vault), apply=True)
    assert results["discovery/proposals.jsonl"]["after"] < \
        results["discovery/proposals.jsonl"]["before"]

    # Rejection STILL stands: distinct evidence (2 entities) is unchanged.
    s3 = promote.promote(str(vault))
    assert not any(tuple(r["identity"]) == ident for r in s3["queued"])

    # ... and NEW evidence from a third entity re-opens it.
    new_rec = {"ts": "2026-07-06T00:00:00+00:00", "from_entity": "e3",
               "from_sources": [], "model": "test", "contract_version": "2.0",
               "proposal": {"kind": "type", "name": "gizmo",
                            "evidence": "x", "confidence": 0.9}}
    existing = [json.loads(line) for line in
                (vault / "discovery" / "proposals.jsonl").read_text(encoding="utf-8").splitlines()]
    _write_proposals(vault, existing + [new_rec])
    s4 = promote.promote(str(vault))
    assert any(tuple(r["identity"]) == ident for r in s4["queued"])

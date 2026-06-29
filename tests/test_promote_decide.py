"""promote.decide() is the deterministic gate that turns aggregated proposals into
auto-promote / queue / defer / reject. Pure function — test the threshold logic
directly without touching the registry."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import promote  # noqa: E402

EMPTY_RS = {
    "tags": [],
    "aliases": [],
    "subtypes_by_type": {},
    "types": [],
    "entity_slugs": [],
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


def test_norm_tag_collapses_surface_forms():
    assert promote.norm_tag("Bank of America") == "bank-of-america"
    assert promote.norm_tag("  MULTI   space ") == "multi-space"
    assert promote.norm_tag(None) == ""


def test_decide_auto_promotes_when_threshold_met():
    agg = _agg("tag", {"kind": "tag", "name": "smart-home"}, sightings=2, from_entities=["a", "b"])
    d = promote.decide(agg, {"new_tag": {"auto": True, "min_sightings": 2}}, {}, EMPTY_RS)
    assert d["action"] == "auto_promote"


def test_decide_defers_below_threshold():
    agg = _agg("tag", {"kind": "tag", "name": "smart-home"}, sightings=1, from_entities=["a"])
    d = promote.decide(agg, {"new_tag": {"auto": True, "min_sightings": 3}}, {}, EMPTY_RS)
    assert d["action"] == "deferred_below_threshold"


def test_decide_rejects_duplicate_already_in_registry():
    agg = _agg("tag", {"kind": "tag", "name": "smart-home"}, sightings=5, from_entities=["a", "b"])
    rs = {**EMPTY_RS, "tags": ["smart-home"]}
    d = promote.decide(agg, {"new_tag": {"auto": True, "min_sightings": 1}}, {}, rs)
    assert d["action"] == "rejected_duplicate"


def test_decide_new_type_is_always_human_gated():
    agg = _agg("type", {"kind": "type", "name": "gizmo"}, sightings=9, from_entities=["a", "b", "c"])
    d = promote.decide(agg, {"new_type": {"auto": False}}, {}, EMPTY_RS)
    assert d["action"] == "queue_for_review"


def test_decide_reclassify_always_queued():
    agg = _agg("reclassify", {"kind": "reclassify", "to_type": "asset"}, sightings=4, from_entities=["a"])
    d = promote.decide(agg, {"reclassify": {"auto": True}}, {}, EMPTY_RS)
    assert d["action"] == "queue_for_review"


def test_decide_no_rule_queues():
    agg = _agg("tag", {"kind": "tag", "name": "x"})
    d = promote.decide(agg, {}, {}, EMPTY_RS)
    assert d["action"] == "queue_for_review"


def test_decide_low_confidence_queued():
    agg = _agg(
        "tag", {"kind": "tag", "name": "smart-home"},
        sightings=3, from_entities=["a", "b", "c"], max_conf=0.0,
    )
    rule = {"new_tag": {"auto": True, "min_sightings": 1, "require_confidence": "high"}}
    d = promote.decide(agg, rule, {}, EMPTY_RS)
    assert d["action"] == "queue_for_review"


# --- new biller / shape / field_mapping kinds ------------------------------

def test_decide_biller_auto_promote_when_target_exists():
    agg = _agg(
        "biller", {"kind": "biller", "id": "acme", "matches": ["acme"],
                   "vendor_slug": "acme", "confidence": 0.9},
        sightings=2, from_entities=["a", "b"],
    )
    rule = {"new_biller": {"auto": True, "min_sightings": 2, "require_target_exists": True}}
    d = promote.decide(agg, rule, {}, _rs(entity_slugs=["vendor/acme"]))
    assert d["action"] == "auto_promote"


def test_decide_biller_queue_when_target_not_in_registry():
    agg = _agg(
        "biller", {"kind": "biller", "id": "acme", "matches": ["acme"],
                   "vendor_slug": "acme", "confidence": 0.9},
        sightings=2, from_entities=["a", "b"],
    )
    rule = {"new_biller": {"auto": True, "min_sightings": 2, "require_target_exists": True}}
    d = promote.decide(agg, rule, {}, _rs(entity_slugs=[]))
    assert d["action"] == "queue_for_review"


def test_decide_biller_idempotent_duplicate():
    agg = _agg(
        "biller", {"kind": "biller", "id": "acme", "matches": ["acme"],
                   "vendor_slug": "acme", "confidence": 0.9},
        sightings=3, from_entities=["a", "b"],
    )
    rule = {"new_biller": {"auto": True, "min_sightings": 1, "require_target_exists": True}}
    d = promote.decide(agg, rule, {}, _rs(biller_ids={"acme"}, entity_slugs=["vendor/acme"]))
    assert d["action"] == "rejected_duplicate"


def test_decide_shape_auto_promote_when_parent_exists():
    agg = _agg(
        "shape", {"kind": "shape", "id": "foo", "type": "document", "subtype": "statement",
                  "keywords": ["x"], "min_matches": 1, "confidence": 0.85},
        sightings=2, from_entities=["a", "b"],
    )
    rule = {"new_shape": {"auto": True, "min_sightings": 2, "require_parent_exists": True}}
    d = promote.decide(agg, rule, {},
                       _rs(types=["document"], subtypes_by_type={"document": ["statement"]}))
    assert d["action"] == "auto_promote"


def test_decide_shape_queue_when_parent_not_in_registry():
    agg = _agg(
        "shape", {"kind": "shape", "id": "foo", "type": "nope", "subtype": "x",
                  "keywords": ["x"], "min_matches": 1, "confidence": 0.85},
        sightings=2, from_entities=["a", "b"],
    )
    rule = {"new_shape": {"auto": True, "min_sightings": 2, "require_parent_exists": True}}
    d = promote.decide(agg, rule, {},
                       _rs(types=["document"], subtypes_by_type={"document": ["statement"]}))
    assert d["action"] == "queue_for_review"


def test_decide_shape_idempotent_duplicate():
    agg = _agg(
        "shape", {"kind": "shape", "id": "foo", "type": "document", "subtype": "statement",
                  "keywords": ["x"], "min_matches": 1, "confidence": 0.85},
        sightings=3, from_entities=["a", "b"],
    )
    rule = {"new_shape": {"auto": True, "min_sightings": 1, "require_parent_exists": True}}
    d = promote.decide(agg, rule, {},
                       _rs(shape_ids={"foo"}, types=["document"],
                           subtypes_by_type={"document": ["statement"]}))
    assert d["action"] == "rejected_duplicate"


def test_decide_field_mapping_always_queued_even_when_valid():
    agg = _agg(
        "field_mapping", {"kind": "field_mapping", "id": "amount",
                          "pattern": r"Amount:\s*\$?(\d+\.\d{2})", "field": "amount",
                          "group": 1, "parse": "float", "evidence_text": "Amount: $12.34",
                          "confidence": 0.95},
        sightings=5, from_entities=["a", "b", "c"],
    )
    rule = {"new_field_mapping": {"auto": False}}
    d = promote.decide(agg, rule, {}, _rs())
    assert d["action"] == "queue_for_review"  # auto:false: ALWAYS human-gated


def test_decide_field_mapping_noncompiling_regex_queued():
    agg = _agg(
        "field_mapping", {"kind": "field_mapping", "id": "amount",
                          "pattern": "(unclosed", "field": "amount", "group": 1,
                          "parse": "text", "evidence_text": "x", "confidence": 0.9},
        sightings=5, from_entities=["a", "b", "c"],
    )
    rule = {"new_field_mapping": {"auto": False}}
    d = promote.decide(agg, rule, {}, _rs())
    assert d["action"] == "queue_for_review"
    assert "compile" in d["reason"] or "regex" in d["reason"]


def test_decide_field_mapping_redos_nested_quantifier_queued():
    # (\d+(?:\s*\d+)*)+ — outer + wraps an inner +, the classic ReDoS shape.
    agg = _agg(
        "field_mapping", {"kind": "field_mapping", "id": "amount",
                          "pattern": r"(\d+(?:\s*\d+)*)+", "field": "amount", "group": 1,
                          "parse": "text", "evidence_text": "12 34", "confidence": 0.9},
        sightings=3, from_entities=["a", "b"],
    )
    rule = {"new_field_mapping": {"auto": False}}
    d = promote.decide(agg, rule, {}, _rs())
    assert d["action"] == "queue_for_review"
    assert "ReDoS" in d["reason"] or "quantifier" in d["reason"]


def test_decide_field_mapping_does_not_match_evidence_queued():
    agg = _agg(
        "field_mapping", {"kind": "field_mapping", "id": "amount",
                          "pattern": r"Total:\s*(\d+)", "field": "amount", "group": 1,
                          "parse": "int", "evidence_text": "nothing relevant here",
                          "confidence": 0.9},
        sightings=3, from_entities=["a", "b"],
    )
    rule = {"new_field_mapping": {"auto": False}}
    d = promote.decide(agg, rule, {}, _rs())
    assert d["action"] == "queue_for_review"
    assert "evidence" in d["reason"] or "match" in d["reason"]


def test_decide_field_mapping_idempotent_duplicate():
    agg = _agg(
        "field_mapping", {"kind": "field_mapping", "id": "amount",
                          "pattern": r"(\d+)", "field": "amount", "group": 1,
                          "parse": "int", "evidence_text": "5", "confidence": 0.9},
        sightings=3, from_entities=["a", "b"],
    )
    rule = {"new_field_mapping": {"auto": False}}
    d = promote.decide(agg, rule, {}, _rs(field_mapping_ids={"amount"}))
    assert d["action"] == "rejected_duplicate"


def test_decide_field_mapping_bad_field_name_queued():
    agg = _agg(
        "field_mapping", {"kind": "field_mapping", "id": "amount",
                          "pattern": r"(\d+)", "field": "Bad Field!", "group": 1,
                          "parse": "int", "evidence_text": "5", "confidence": 0.9},
        sightings=3, from_entities=["a", "b"],
    )
    rule = {"new_field_mapping": {"auto": False}}
    d = promote.decide(agg, rule, {}, _rs())
    assert d["action"] == "queue_for_review"
    assert "field" in d["reason"]

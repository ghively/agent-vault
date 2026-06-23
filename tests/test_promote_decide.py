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
}


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

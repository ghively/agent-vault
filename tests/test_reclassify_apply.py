"""Coverage for reclassify_apply.py — the human-approved step that physically
re-files an entity across type folders and rewrites every cross-reference.

This module had ZERO tests despite being the highest-risk mutating pass: it
renames entity files (os.replace) and rewrites `related:` refs across *every
other* entity, so a bug scatters dangling refs vault-wide. The suite exercises:

  - queued_reclassifies last-action-wins ledger logic (open / closed / re-opened);
  - a full type-change apply: file move, frontmatter rewrite, cross-ref rewrite
    + LINKS-block rebuild in a second entity, and the reclassify_applied ledger record;
  - subtype-only change (file stays put);
  - --dry-run mutates nothing but still counts refs;
  - idempotent re-run (a second apply_all is a no-op);
  - --slug targeting;
  - schema validation (bad to_type / to_subtype -> skip);
  - ambiguous-slug integrity guard;
  - surgical ref rewriting (flow-style list; prose and other lists untouched).

A sandbox copy of the repo supplies a real registry/schema.yaml; entities and
the promoted.jsonl ledger are authored per-test for precise control.
"""
import os
import shutil
from pathlib import Path

import pytest

from agent_vault import reclassify_apply as rc

HERE = Path(__file__).resolve().parent
VAULT_SRC = HERE.parent


def _sandbox(tmp_path):
    dst = tmp_path / "vault"
    shutil.copytree(VAULT_SRC, dst, ignore=shutil.ignore_patterns("tests", "__pycache__", ".git"))
    # Start from a clean entity tree so cross-ref assertions are deterministic.
    for sub in os.listdir(dst / "entities"):
        p = dst / "entities" / sub
        if p.is_dir():
            for f in p.glob("*.md"):
                f.unlink()
    return dst


def _write_entity(vault, type_, slug, *, subtype="statement", related=None,
                  tags=None, extra_body=""):
    related = related or []
    d = vault / "entities" / type_
    d.mkdir(parents=True, exist_ok=True)
    rel_fm = "".join(f"  - {r}\n" for r in related)
    tags_line = f"tags: [{', '.join(tags)}]\n" if tags else ""
    links_inner = "".join(f"- Related: [[{r}]]\n" for r in related)
    text = (
        f"---\n"
        f"slug: {slug}\n"
        f"type: {type_}\n"
        f"subtype: {subtype}\n"
        f"title: {slug.title()}\n"
        f"status: compiled\n"
        f"sources:\n  - raw/documents/{slug}.pdf\n"
        f"{tags_line}"
        f"related:\n{rel_fm}"
        f"---\n\n"
        f"<!-- LINKS:BEGIN -->\n{links_inner}<!-- LINKS:END -->\n\n"
        f"Prose body for {slug}. {extra_body}\n"
    )
    (d / f"{slug}.md").write_text(text, encoding="utf-8")
    return d / f"{slug}.md"


def _queue(vault, *, slug, to_type, to_subtype, identity=None):
    """Append a queue_for_review reclassify record to promoted.jsonl."""
    identity = identity or ["reclassify", slug]
    rec = {
        "ts": "2026-01-01T00:00:00+00:00", "kind": "reclassify",
        "action": "queue_for_review", "identity": identity,
        "from_entities": [slug],
        "proposal": {"to_type": to_type, "to_subtype": to_subtype,
                     "reason": "test"},
    }
    rc._append_log(vault, rec)
    return tuple(identity)


def _ledger(vault):
    return rc.load_jsonl(str(vault / "discovery" / "promoted.jsonl"))


# ---------------------------------------------------------------------------
# queued_reclassifies — last-action-wins ledger state machine
# ---------------------------------------------------------------------------

def test_queued_reclassifies_open_closed_reopened():
    ident = ("reclassify", "foo")
    other = lambda action, **kw: {"kind": "reclassify", "action": action,  # noqa: E731
                                  "identity": list(ident), **kw}
    # open: only a queue
    log = [other("queue_for_review", proposal={"to_type": "document"})]
    assert [i for i, _, _ in rc.queued_reclassifies(log)] == [ident]
    # closed: applied after queue
    log2 = log + [other("reclassify_applied")]
    assert rc.queued_reclassifies(log2) == []
    # blocked: review_rejected after queue
    log3 = log + [other("review_rejected")]
    assert rc.queued_reclassifies(log3) == []
    # re-opened: a later queue after a rejection
    log4 = log3 + [other("queue_for_review", proposal={"to_type": "asset"})]
    assert [i for i, _, _ in rc.queued_reclassifies(log4)] == [ident]
    # review_approved keeps it pending (approval awaiting apply)
    log5 = log + [other("review_approved")]
    assert [i for i, _, _ in rc.queued_reclassifies(log5)] == [ident]


# ---------------------------------------------------------------------------
# full type-change apply
# ---------------------------------------------------------------------------

def test_type_change_moves_file_rewrites_fm_and_refs(tmp_path):
    vault = _sandbox(tmp_path)
    # target entity currently filed as asset/appliance, to be re-filed document/manual
    _write_entity(vault, "asset", "space-heater", subtype="appliance")
    # a second entity that references it — must get its ref + LINKS block rewritten
    referrer = _write_entity(vault, "account", "bofa-checking", subtype="checking",
                             related=["asset/space-heater"])
    _queue(vault, slug="space-heater", to_type="document", to_subtype="manual")

    out = rc.apply_all(str(vault))
    assert len(out) == 1 and out[0]["status"] == "applied"
    assert out[0]["refs_updated"] == 1

    # file physically moved
    assert not (vault / "entities" / "asset" / "space-heater.md").exists()
    moved = vault / "entities" / "document" / "space-heater.md"
    assert moved.exists()
    body = moved.read_text(encoding="utf-8")
    assert "type: document" in body and "subtype: manual" in body

    # referrer's related ref AND LINKS block both rewritten to the new ref
    rtext = referrer.read_text(encoding="utf-8")
    assert "asset/space-heater" not in rtext
    assert "- document/space-heater" in rtext
    assert "[[document/space-heater]]" in rtext
    assert "[[asset/space-heater]]" not in rtext

    # ledger closed with reclassify_applied so a re-run is a no-op
    actions = [r["action"] for r in _ledger(vault) if r.get("kind") == "reclassify"]
    assert "reclassify_started" in actions
    assert actions[-1] == "reclassify_applied"


def test_idempotent_rerun_is_noop(tmp_path):
    vault = _sandbox(tmp_path)
    _write_entity(vault, "asset", "space-heater", subtype="appliance")
    _queue(vault, slug="space-heater", to_type="document", to_subtype="manual")
    first = rc.apply_all(str(vault))
    assert first[0]["status"] == "applied"
    # second run: the identity is closed, nothing queued
    second = rc.apply_all(str(vault))
    assert second == []


def test_subtype_only_change_keeps_file_in_place(tmp_path):
    vault = _sandbox(tmp_path)
    _write_entity(vault, "asset", "old-laptop", subtype="appliance")
    _queue(vault, slug="old-laptop", to_type="asset", to_subtype="computer")
    out = rc.apply_all(str(vault))
    assert out[0]["status"] == "applied"
    assert out[0]["moves_file"] is False
    p = vault / "entities" / "asset" / "old-laptop.md"
    assert p.exists()                          # not moved
    assert "subtype: computer" in p.read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# dry-run
# ---------------------------------------------------------------------------

def test_dry_run_mutates_nothing_but_counts_refs(tmp_path):
    vault = _sandbox(tmp_path)
    src = _write_entity(vault, "asset", "space-heater", subtype="appliance")
    referrer = _write_entity(vault, "account", "acct", subtype="checking",
                             related=["asset/space-heater"])
    before_src = src.read_text(encoding="utf-8")
    before_ref = referrer.read_text(encoding="utf-8")
    _queue(vault, slug="space-heater", to_type="document", to_subtype="manual")

    out = rc.apply_all(str(vault), dry_run=True)
    assert out[0]["status"] == "applied"
    assert out[0]["refs_updated"] == 1
    # nothing on disk changed, including the ledger (no reclassify_started)
    assert src.read_text(encoding="utf-8") == before_src
    assert referrer.read_text(encoding="utf-8") == before_ref
    assert (vault / "entities" / "asset" / "space-heater.md").exists()
    assert not (vault / "entities" / "document" / "space-heater.md").exists()
    actions = [r["action"] for r in _ledger(vault) if r.get("kind") == "reclassify"]
    assert actions == ["queue_for_review"]


# ---------------------------------------------------------------------------
# --slug targeting
# ---------------------------------------------------------------------------

def test_slug_targeting_applies_only_named_entity(tmp_path):
    vault = _sandbox(tmp_path)
    _write_entity(vault, "asset", "aaa", subtype="appliance")
    _write_entity(vault, "asset", "bbb", subtype="appliance")
    _queue(vault, slug="aaa", to_type="document", to_subtype="manual")
    _queue(vault, slug="bbb", to_type="document", to_subtype="manual")

    out = rc.apply_all(str(vault), only_slug="aaa")
    assert [r["slug"] for r in out] == ["aaa"]
    assert (vault / "entities" / "document" / "aaa.md").exists()
    assert (vault / "entities" / "asset" / "bbb.md").exists()      # untouched


# ---------------------------------------------------------------------------
# schema validation -> skip
# ---------------------------------------------------------------------------

def test_bad_target_type_is_skipped(tmp_path):
    vault = _sandbox(tmp_path)
    _write_entity(vault, "asset", "x", subtype="appliance")
    _queue(vault, slug="x", to_type="nonsuch", to_subtype="whatever")
    out = rc.apply_all(str(vault))
    assert out[0]["status"] == "skip"
    assert "not in schema" in out[0]["reason"]
    assert (vault / "entities" / "asset" / "x.md").exists()         # untouched


def test_bad_target_subtype_is_skipped(tmp_path):
    vault = _sandbox(tmp_path)
    _write_entity(vault, "asset", "x", subtype="appliance")
    _queue(vault, slug="x", to_type="document", to_subtype="not-a-subtype")
    out = rc.apply_all(str(vault))
    assert out[0]["status"] == "skip"
    assert "subtype" in out[0]["reason"]


def test_missing_entity_is_skipped(tmp_path):
    vault = _sandbox(tmp_path)
    _queue(vault, slug="ghost", to_type="document", to_subtype="manual")
    out = rc.apply_all(str(vault))
    assert out[0]["status"] == "skip"
    assert "no entity" in out[0]["reason"]


# ---------------------------------------------------------------------------
# integrity guard — same slug under two type dirs
# ---------------------------------------------------------------------------

def test_ambiguous_slug_raises_and_is_reported_as_skip(tmp_path):
    vault = _sandbox(tmp_path)
    _write_entity(vault, "asset", "dup", subtype="appliance")
    _write_entity(vault, "document", "dup", subtype="manual")
    with pytest.raises(RuntimeError, match="multiple type dirs"):
        rc.find_entity_by_slug(str(vault), "dup")
    # surfaced as a skip (not a crash) through the driver
    _queue(vault, slug="dup", to_type="account", to_subtype="checking")
    out = rc.apply_all(str(vault))
    assert out[0]["status"] == "skip"
    assert "multiple type dirs" in out[0]["reason"]


# ---------------------------------------------------------------------------
# surgical ref rewriting — flow style, prose/other lists untouched
# ---------------------------------------------------------------------------

def test_rewrite_related_flow_style():
    fm = "slug: a\nrelated: [asset/space-heater, other/keep]\n"
    out = rc._rewrite_related_in_fm(fm, "asset/space-heater", "document/space-heater")
    assert "document/space-heater" in out
    assert "other/keep" in out
    assert "asset/space-heater" not in out


def test_rewrite_related_missing_ref_returns_unchanged():
    fm = "slug: a\nrelated:\n  - other/keep\n"
    out = rc._rewrite_related_in_fm(fm, "asset/space-heater", "document/space-heater")
    assert out == fm


def test_rewrite_refs_leaves_prose_and_other_lists_untouched(tmp_path):
    vault = _sandbox(tmp_path)
    # `sources:` coincidentally contains the same string; prose mentions it too.
    d = vault / "entities" / "account"
    d.mkdir(parents=True, exist_ok=True)
    text = (
        "---\n"
        "slug: coincidence\ntype: account\nsubtype: checking\n"
        "title: C\nstatus: compiled\n"
        "sources:\n  - asset/space-heater\n"          # NOT a related ref
        "related:\n  - asset/space-heater\n"          # the real target
        "---\n\n"
        "<!-- LINKS:BEGIN -->\n- Related: [[asset/space-heater]]\n<!-- LINKS:END -->\n\n"
        "Prose mentions asset/space-heater which must remain verbatim.\n"
    )
    (d / "coincidence.md").write_text(text, encoding="utf-8")

    n = rc.rewrite_refs(str(vault), "asset/space-heater", "document/space-heater")
    assert n == 1
    out = (d / "coincidence.md").read_text(encoding="utf-8")
    # related: rewritten; sources: and prose left alone
    assert "related:\n  - document/space-heater" in out
    assert "sources:\n  - asset/space-heater" in out
    assert "Prose mentions asset/space-heater which must remain verbatim." in out

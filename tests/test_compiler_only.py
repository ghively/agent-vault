"""--only compiles a single targeted entity. Uses the MockClient (offline).

Also covers the v2.0 compile contract: the three new proposal kinds
(new_biller / new_shape / new_field_mapping) are surfaced by parse_response
rather than silently dropped, and malformed new-kind proposals are still
surfaced (not silently dropped)."""
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

from agent_vault import compiler

HERE = Path(__file__).resolve().parent
VAULT_SRC = HERE.parent


def _sandbox(tmp_path):
    dst = tmp_path / "vault"
    shutil.copytree(VAULT_SRC, dst, ignore=shutil.ignore_patterns("tests", "__pycache__"))
    return dst


def test_only_compiles_just_one_entity(tmp_path):
    vault = _sandbox(tmp_path)
    # Make two entities into stubs so both are pending.
    for ref in ("asset/carrier-furnace.md", "account/bofa-checking.md"):
        p = vault / "entities" / ref
        text = p.read_text(encoding="utf-8").replace("status: compiled", "status: stub", 1)
        p.write_text(text, encoding="utf-8")
    env = {**os.environ, "AGENT_VAULT_COMPILER": "mock"}
    # Run as a module since it's now in a package
    out = subprocess.run([sys.executable, "-m", "agent_vault.compiler", ".", "--only", "carrier-furnace"],
                         cwd=str(vault), capture_output=True, text=True, env=env)
    assert out.returncode == 0, out.stderr
    assert "carrier-furnace" in out.stdout
    assert "bofa-checking" not in out.stdout  # the other stub was NOT touched


# ---------------------------------------------------------------------------
# v2.0 compile-contract coverage
# ---------------------------------------------------------------------------

def _resp(proposals):
    """Render a model response with the given proposals list and parse it."""
    body = json.dumps(proposals)
    raw = f"<prose>ok</prose>\n<proposals>{body}</proposals>"
    prose, props = compiler.parse_response(raw)
    return prose, props


def test_contract_version_bumped():
    assert compiler.PROMPT_CONTRACT_VERSION == "2.0"
    # the new kinds are documented in the prompt itself
    assert "biller" in compiler.SYSTEM_PROMPT
    assert "shape" in compiler.SYSTEM_PROMPT
    assert "field_mapping" in compiler.SYSTEM_PROMPT


def test_new_biller_proposal_is_surfaced():
    p = {"kind": "biller", "id": "acme-corp", "matches": ["acme.com", "acme"],
         "vendor_slug": "acme", "default_tags": ["retail"],
         "confidence": 0.8, "evidence": "saw acme.com", "evidence_text": "acme.com"}
    _, props = _resp([p])
    assert len(props) == 1
    assert props[0]["kind"] == "biller"
    assert props[0]["matches"] == ["acme.com", "acme"]


def test_new_shape_proposal_is_surfaced():
    p = {"kind": "shape", "id": "tax-1098", "type": "document",
         "subtype": "tax", "keywords": ["mortgage interest", "form 1098"],
         "min_matches": 2, "confidence": 0.7, "evidence": "co-occurring",
         "evidence_text": "form 1098"}
    _, props = _resp([p])
    assert len(props) == 1
    assert props[0]["kind"] == "shape"
    assert props[0]["min_matches"] == 2


def test_new_field_mapping_proposal_is_surfaced():
    p = {"kind": "field_mapping", "id": "rewards", "applies_to": ["pdf"],
         "pattern": r"Rewards:\s*(\d+)", "field": "rewards_points", "group": 1,
         "parse": "int", "require_digit": True, "confidence": 0.6,
         "evidence": "rewards line", "evidence_text": "Rewards: 500"}
    _, props = _resp([p])
    assert len(props) == 1
    assert props[0]["kind"] == "field_mapping"
    assert props[0]["field"] == "rewards_points"


def test_new_kinds_not_silently_dropped_alongside_old_kinds():
    # old kinds and new kinds interleave; all should be surfaced
    proposals = [
        {"kind": "tag", "name": "banking"},
        {"kind": "biller", "id": "x", "matches": ["x"], "evidence_text": "x"},
        {"kind": "shape", "id": "y", "type": "document", "subtype": "tax",
         "keywords": ["k"], "min_matches": 1, "evidence_text": "y"},
        {"kind": "field_mapping", "id": "z", "pattern": "a", "field": "f",
         "evidence_text": "z"},
    ]
    _, props = _resp(proposals)
    assert {p["kind"] for p in props} == {"tag", "biller", "shape",
                                          "field_mapping"}


def test_malformed_new_kind_proposal_dict_without_kind_is_dropped():
    # a dict with no `kind` key is the only thing dropped by parse_response;
    # a new-kind dict missing its payload fields is STILL surfaced (promote.py
    # is responsible for validation, not the parser).
    proposals = [
        {"kind": "field_mapping"},          # minimal, payload incomplete
        {"no_kind_here": True},                  # dropped: no `kind` key
        {"kind": "biller"},                  # minimal
    ]
    _, props = _resp(proposals)
    kinds = [p["kind"] for p in props]
    assert kinds == ["field_mapping", "biller"]


def test_malformed_proposals_json_is_surfaced_not_silently_dropped():
    # malformed JSON in <proposals> yields [] and a stderr warning (not silent)
    raw = "<prose>ok</prose>\n<proposals>[{bad json}</proposals>"
    prose, props = compiler.parse_response(raw)
    assert prose == "ok"
    assert props == []


def test_mock_client_still_parses_cleanly_with_v2_contract():
    mc = compiler.MockClient()
    user = ("<vocabulary>\ntypes: document\n</vocabulary>\n<stub>\n"
            "slug: x\ntitle: X\ntype: document\nsubtype: statement\n</stub>\n"
            "<sources>\n[source: a.pdf]\nfoo\n</sources>")
    r = mc.compile(compiler.SYSTEM_PROMPT, user)
    assert r["proposals"] == []           # mock emits no proposals — fine
    assert r["prose"].strip()              # prose body present


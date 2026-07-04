"""Unit coverage for compiler internals hardened in the optimization pass:

  - parse_response proposal-slicing robustness (proposals parsed only after the
    real </prose>, so prose that quotes "<proposals>" can't hijack the match);
  - find_pending drift guard (a compiled entity with a missing/empty
    sources_hash must NOT be re-yielded forever — the empty-hash recompile loop).
"""

from pathlib import Path

from agent_vault import compiler


# --- parse_response ---------------------------------------------------------

def test_parse_response_basic_prose_and_proposals():
    raw = ('<prose>The furnace runs on gas.</prose>\n'
           '<proposals>[{"kind": "tag", "name": "hvac"}]</proposals>')
    prose, proposals = compiler.parse_response(raw)
    assert prose == "The furnace runs on gas."
    assert proposals == [{"kind": "tag", "name": "hvac"}]


def test_parse_response_prose_quoting_proposals_tag_does_not_drop_real_proposals():
    """Regression (#12): prose that literally contains the string '<proposals>'
    must not cause the real proposals block after </prose> to be mis-sliced by
    the greedy PROPOSALS_RE and dropped as non-JSON."""
    raw = ('<prose>The compile contract emits a <proposals> block; see docs.</prose>\n'
           '<proposals>[{"kind": "tag", "name": "heating"}]</proposals>')
    prose, proposals = compiler.parse_response(raw)
    assert "<proposals>" in prose  # the literal prose mention survives
    assert proposals == [{"kind": "tag", "name": "heating"}]


def test_parse_response_malformed_proposals_yield_empty(capsys):
    raw = '<prose>ok</prose>\n<proposals>{not json}</proposals>'
    prose, proposals = compiler.parse_response(raw)
    assert prose == "ok"
    assert proposals == []
    assert "malformed" in capsys.readouterr().err


def test_parse_response_strips_injected_frontmatter_and_links():
    raw = ('<prose>Real text.\n---\n<!-- LINKS:BEGIN -->\ngarbage\n'
           '<!-- LINKS:END --></prose>')
    prose, _ = compiler.parse_response(raw)
    assert "LINKS:BEGIN" not in prose
    assert "LINKS:END" not in prose


# --- find_pending drift guard ----------------------------------------------

def _entity(vault: Path, slug: str, status: str, *, sources_hash=None,
            compiled_from_hash=None) -> None:
    d = vault / "entities" / "asset"
    d.mkdir(parents=True, exist_ok=True)
    lines = ["---", f"slug: {slug}", "type: asset", "subtype: general",
             f"title: {slug}", f"status: {status}"]
    if sources_hash is not None:
        lines.append(f"sources_hash: {sources_hash}")
    if compiled_from_hash is not None:
        lines.append(f"compiled_from_hash: {compiled_from_hash}")
    lines += ["---", "", "<!-- LINKS:BEGIN -->", "<!-- LINKS:END -->", "", "Body."]
    (d / f"{slug}.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def _pending_slugs(vault: Path) -> set[str]:
    return {fm.get("slug") for _p, _r, _f, fm in compiler.find_pending(str(vault))}


def test_find_pending_yields_stubs(tmp_path):
    _entity(tmp_path, "a-stub", "stub", sources_hash="h1")
    assert "a-stub" in _pending_slugs(tmp_path)


def test_find_pending_yields_drifted_compiled(tmp_path):
    _entity(tmp_path, "drifted", "compiled", sources_hash="h2",
            compiled_from_hash="h1")
    assert "drifted" in _pending_slugs(tmp_path)


def test_find_pending_skips_up_to_date_compiled(tmp_path):
    _entity(tmp_path, "current", "compiled", sources_hash="h1",
            compiled_from_hash="h1")
    assert "current" not in _pending_slugs(tmp_path)


def test_find_pending_does_not_loop_on_empty_sources_hash(tmp_path):
    """Regression (#4): a compiled entity missing sources_hash must NOT be
    treated as perpetually drifted (which would recompile it every pass and
    re-append proposals, burning LLM tokens forever)."""
    _entity(tmp_path, "no-hash", "compiled", compiled_from_hash="whatever")
    assert "no-hash" not in _pending_slugs(tmp_path)
    # Also the empty-string case.
    _entity(tmp_path, "empty-hash", "compiled", sources_hash="",
            compiled_from_hash="")
    assert "empty-hash" not in _pending_slugs(tmp_path)

"""ingest.py writer/extractor hardening: ambiguous YAML scalars must
round-trip as strings, HTML-only emails must keep their body, a corrupt PDF
must degrade instead of poisoning the run, and duplicate content at a second
path must get a manifest entry so lint stops flagging it."""
import re
import shutil
from pathlib import Path

import yaml

from agent_vault import ingest, lint

HERE = Path(__file__).resolve().parent
VAULT_SRC = HERE.parent


def _sandbox(tmp_path):
    dst = tmp_path / "vault"
    shutil.copytree(VAULT_SRC, dst, ignore=shutil.ignore_patterns("tests", "__pycache__"))
    return dst


def _parse_frontmatter(text):
    m = re.match(r"^---\n(.*?)\n---\n", text, re.S)
    assert m, "stub must have a frontmatter block"
    return yaml.safe_load(m.group(1))


# ---------------------------------------------------------------------------
# YAML scalar quoting
# ---------------------------------------------------------------------------

def test_render_stub_roundtrips_ambiguous_scalars():
    stub = {
        "slug": "ambiguous-scalars", "type": "document", "subtype": "receipt",
        "title": "1984",            # YAML would read an int
        "status": "stub", "confidence": 0.9, "created": "2026-07-04",
        "sources": ["raw/misc/a.txt"], "sources_hash": "abc123",
        "tags": [], "related": [],
        "serial": "no",             # YAML would read False
        "last4": "0234",            # YAML 1.1 would read octal 156
        "model": "007",             # YAML would read int 7
    }
    fm = _parse_frontmatter(ingest.render_stub(stub))
    assert fm["title"] == "1984"
    assert fm["serial"] == "no"
    assert fm["last4"] == "0234"
    assert fm["model"] == "007"


def test_yaml_str_quotes_only_when_needed():
    assert ingest._yaml_str("Bank of America") == "Bank of America"
    assert yaml.safe_load("k: " + ingest._yaml_str("no")) == {"k": "no"}
    assert yaml.safe_load("k: " + ingest._yaml_str("0234")) == {"k": "0234"}
    assert yaml.safe_load("k: " + ingest._yaml_str("a: b")) == {"k": "a: b"}
    assert yaml.safe_load("k: " + ingest._yaml_str("line\nbreak")) == {"k": "line\nbreak"}
    assert yaml.safe_load("k: " + ingest._yaml_str("")) == {"k": ""}


def test_collections_importer_yaml_quote_roundtrips():
    from agent_vault.collections_importer import _yaml_quote
    for hostile in ("1984", "no", "0234", "yes", "null", "3.14",
                    "title: with colon", " padded "):
        assert yaml.safe_load("k: " + _yaml_quote(hostile)) == {"k": hostile}


# ---------------------------------------------------------------------------
# Extractors
# ---------------------------------------------------------------------------

def test_extract_email_html_only_body(tmp_path):
    eml = (b"From: billing@example.com\r\n"
           b"To: gene@example.com\r\n"
           b"Subject: Statement ready\r\n"
           b"MIME-Version: 1.0\r\n"
           b"Content-Type: text/html; charset=utf-8\r\n"
           b"\r\n"
           b"<html><body><style>p{color:red}</style>"
           b"<p>Your total due is $42.17</p></body></html>\r\n")
    p = tmp_path / "html-only.eml"
    p.write_bytes(eml)
    ex = ingest.extract_email(str(p))
    assert "total due is $42.17" in ex["text"]
    assert "color:red" not in ex["text"]  # style content stays dropped


def test_extract_email_prefers_plain_text_when_present(tmp_path):
    eml = (b"From: a@example.com\r\nSubject: alt\r\nMIME-Version: 1.0\r\n"
           b"Content-Type: multipart/alternative; boundary=XX\r\n\r\n"
           b"--XX\r\nContent-Type: text/plain\r\n\r\nplain body here\r\n"
           b"--XX\r\nContent-Type: text/html\r\n\r\n<p>html body here</p>\r\n"
           b"--XX--\r\n")
    p = tmp_path / "alt.eml"
    p.write_bytes(eml)
    ex = ingest.extract_email(str(p))
    assert "plain body here" in ex["text"]
    assert "html body here" not in ex["text"]


def test_extract_pdf_degrades_on_corrupt_file(tmp_path):
    p = tmp_path / "corrupt.pdf"
    p.write_bytes(b"%PDF-1.4\nthis is not really a pdf \xff\xfe\x00garbage")
    ex = ingest.extract_pdf(str(p))  # must not raise
    assert ex["text"] == ""
    assert "warn" in ex["meta"]


# ---------------------------------------------------------------------------
# Duplicate content at a second path
# ---------------------------------------------------------------------------

def test_duplicate_second_path_gets_manifest_entry(tmp_path):
    vault = _sandbox(tmp_path)
    misc = vault / "raw" / "misc"
    misc.mkdir(parents=True, exist_ok=True)
    content = "grocery receipt\norder number: ABC1234567\n"
    (misc / "aaa-original.txt").write_text(content, encoding="utf-8")
    (misc / "bbb-copy.txt").write_text(content, encoding="utf-8")

    summary = ingest.ingest(str(vault))
    assert summary["new"] == 1
    assert summary["skipped"] == 1

    manifest = ingest.load_manifest(str(vault))
    by_path = {r.get("path"): r for r in manifest}
    assert "raw/misc/aaa-original.txt" in by_path
    dup = by_path.get("raw/misc/bbb-copy.txt")
    assert dup is not None, "duplicate path must get its own manifest entry"
    assert dup["kind"] == "duplicate"
    assert dup["duplicate_of"] == "raw/misc/aaa-original.txt"
    assert not dup.get("error"), "a duplicate is not an ingest error"

    # lint's drift check now sees both paths as ingested.
    assert lint.check_manifest_drift(str(vault)) == []

    # Re-running is idempotent: no second duplicate record.
    summary2 = ingest.ingest(str(vault))
    assert summary2["new"] == 0
    manifest2 = ingest.load_manifest(str(vault))
    dups = [r for r in manifest2 if r.get("kind") == "duplicate"]
    assert len(dups) == 1

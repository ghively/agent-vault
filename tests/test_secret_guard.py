"""The spec §7 write-side secret guard and the compiler's fact protection both
hinge on intra-package imports that must never silently degrade to no-ops:
ingest/validate/promote must see the packaged secret_scan module, and the
compiler's denoise/truncate helpers must see ingest's fact regexes."""
import re
import shutil
import subprocess
import sys
from pathlib import Path

from agent_vault import compiler, ingest, promote

HERE = Path(__file__).resolve().parent
VAULT_SRC = HERE.parent

FAKE_GITHUB_TOKEN = "ghp_" + "a" * 36  # matches secret_scan's github_token shape


def _sandbox(tmp_path):
    dst = tmp_path / "vault"
    shutil.copytree(VAULT_SRC, dst, ignore=shutil.ignore_patterns("tests", "__pycache__"))
    return dst


def _run_validate(vault):
    return subprocess.run(
        [sys.executable, "-m", "agent_vault.validate", "."],
        cwd=str(vault),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )


def test_ingest_secret_scan_is_wired():
    # The write-side guard must be the real module, never a silent None.
    assert ingest.secret_scan is not None
    assert ingest.secret_scan.scan(FAKE_GITHUB_TOKEN), "scan() must flag a token"


def test_validate_rejects_secret_in_frontmatter(tmp_path):
    vault = _sandbox(tmp_path)
    target = next((vault / "entities").glob("*/*.md"))
    text = target.read_text(encoding="utf-8")
    corrupted = text.replace("---\n", f"---\ntoken_hint: {FAKE_GITHUB_TOKEN}\n", 1)
    assert corrupted != text
    target.write_text(corrupted, encoding="utf-8")
    out = _run_validate(vault)
    assert out.returncode == 1, out.stdout + out.stderr
    assert "secret" in out.stdout


def test_promote_field_mapping_gate_rejects_secret_shaped_capture():
    p = {"id": "token-grab", "field": "token_hint",
         "pattern": r"Token:\s*(\S+)", "group": 1, "parse": "text",
         "evidence_text": f"Token: {FAKE_GITHUB_TOKEN}", "confidence": 0.9}
    rs = {"_secret_scan": promote._load_secret_scan(".")}
    ok, reason = promote._validate_field_mapping(p, rs)
    assert not ok
    assert "secret" in reason


def test_build_stub_downgrades_secretful_source_to_needs_review():
    classification = {"type": "document", "subtype": "receipt",
                      "confidence": 0.95, "biller": None, "shape": None,
                      "tags": [], "related": []}
    subject = {"name": "raw/misc/leak.txt", "kind": "text",
               "source_ref": "raw/misc/leak.txt", "meta": {},
               "text": f"order confirmation\napi_key = {FAKE_GITHUB_TOKEN}\n"}
    stub = ingest.build_stub(subject, classification, {})
    assert stub["status"] == "needs-review"
    assert "secret_scan" in stub["notes"]


def test_fact_regexes_nonempty_and_truncate_preserves_facts():
    fact_res = compiler._fact_regexes()
    assert fact_res, "_fact_regexes must import ingest's regexes, never cache []"
    filler = "lorem ipsum filler line with no facts whatsoever\n" * 100
    fact_line = "Payment Due Date: 2026-08-01"
    text = filler + fact_line
    out = compiler.priority_truncate(text, 500)
    assert len(out) < len(text)
    assert "2026-08-01" in out, "fact-bearing line beyond the cap must survive"


def test_denoise_never_drops_fact_lines():
    fact_line = "Total Due: $123.45"
    text = ("> quoted line one\n> quoted line two\n> quoted line three\n"
            "> quoted line four\n" + fact_line + "\n")
    out = compiler.denoise_text(text)
    assert fact_line in out
    assert not re.search(r"^> quoted line four$", out, re.M)

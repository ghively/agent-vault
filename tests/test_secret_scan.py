"""Coverage for secret_scan.py — the write-side secret guard.

Before this file the module was only exercised indirectly (ingest's broad-tier
downgrade and validate's hard-fail gate). Its pure detection functions —
looks_like_secret (strict tier), redact, and scan (broad tier: labeled values,
Luhn-checked cards) — had no direct unit tests. This covers them at the
boundaries, and asserts the module's core contract: a finding never carries the
secret itself, only a kind + a short fingerprint.

All tokens below are format-valid but FAKE (canonical example keys / standard
test card numbers) — no real credential is present.
"""
from agent_vault import secret_scan as ss

# Canonical fake credentials (public example values, not real secrets).
AWS_KEY = "AKIAIOSFODNN7EXAMPLE"                       # AWS's own docs example
GH_TOKEN = "ghp_" + "A1b2C3d4E5f6G7h8I9j0K1l2M3n4O5p6Q7r8"   # ghp_ + 36
GOOGLE_KEY = "AIza" + "B" * 35                          # AIza + 35
PRIVATE_KEY = "-----BEGIN RSA PRIVATE KEY-----"
JWT = "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.abcDEF123456"
SLACK = "xoxb-1234567890-abcdEFGHijklMNOP"
VISA_TEST = "4111 1111 1111 1111"                       # standard Luhn-valid test PAN
AMEX_TEST = "3782 822463 10005"                         # standard Amex test PAN (4-6-5)


# ---------------------------------------------------------------------------
# looks_like_secret — strict tier (validate's gate)
# ---------------------------------------------------------------------------

def test_looks_like_secret_recognizes_each_strict_format():
    assert ss.looks_like_secret(AWS_KEY) == "aws_access_key_id"
    assert ss.looks_like_secret(GH_TOKEN) == "github_token"
    assert ss.looks_like_secret(GOOGLE_KEY) == "google_api_key"
    assert ss.looks_like_secret(PRIVATE_KEY) == "private_key_block"
    assert ss.looks_like_secret(JWT) == "jwt"
    assert ss.looks_like_secret(SLACK) == "slack_token"


def test_looks_like_secret_ignores_ordinary_values():
    for ordinary in ["Carrier Furnace (Basement)", "bofa-checking",
                     "password: required", "1234", ""]:
        assert ss.looks_like_secret(ordinary) is None


def test_looks_like_secret_rejects_non_strings():
    assert ss.looks_like_secret(None) is None
    assert ss.looks_like_secret(4200) is None
    assert ss.looks_like_secret(["AKIAIOSFODNN7EXAMPLE"]) is None


# ---------------------------------------------------------------------------
# redact — replace strict secrets with [REDACTED:<kind>]
# ---------------------------------------------------------------------------

def test_redact_replaces_secret_and_keeps_surrounding_text():
    out = ss.redact(f"key is {AWS_KEY} ok")
    assert AWS_KEY not in out
    assert "[REDACTED:aws_access_key_id]" in out
    assert out.startswith("key is ") and out.endswith(" ok")


def test_redact_leaves_ordinary_text_and_non_strings_untouched():
    assert ss.redact("just a normal title") == "just a normal title"
    assert ss.redact("") == ""
    assert ss.redact(None) is None
    assert ss.redact(42) == 42


# ---------------------------------------------------------------------------
# scan — broad tier (ingest's downgrade trigger)
# ---------------------------------------------------------------------------

def test_scan_empty_and_clean_text():
    assert ss.scan("") == []
    assert ss.scan(None) == []
    assert ss.scan("A perfectly ordinary sentence about a furnace.") == []


def test_scan_finds_strict_secret_without_leaking_value():
    findings = ss.scan(f"config:\n  aws = {AWS_KEY}\n")
    assert len(findings) == 1
    f = findings[0]
    assert f["kind"] == "aws_access_key_id"
    # contract: only kind + fingerprint, never the secret text
    assert set(f) == {"kind", "fingerprint"}
    assert AWS_KEY not in f["fingerprint"]
    assert len(f["fingerprint"]) == 8


def test_scan_labeled_value_gating():
    # a real-looking value trips it; a dictionary word does not
    assert any(f["kind"] == "labeled_secret"
               for f in ss.scan("password: s3cr3t-Hunter2"))
    assert ss.scan("password: required") == []
    assert ss.scan("secret: yes") == []          # too short / not secretish


def test_scan_dedupes_repeated_secret():
    text = f"{AWS_KEY}\n{AWS_KEY}\n{AWS_KEY}"
    assert len(ss.scan(text)) == 1               # one finding despite 3 sightings


def test_scan_credit_card_luhn_and_prefix():
    visa = ss.scan(f"card on file: {VISA_TEST}")
    assert [f["kind"] for f in visa] == ["credit_card"]
    amex = ss.scan(f"amex: {AMEX_TEST}")
    assert [f["kind"] for f in amex] == ["credit_card"]
    # grouped 16-digit run that FAILS Luhn is not flagged as a card
    assert ss.scan("ref 4111 1111 1111 1112") == []
    # a grouped run whose first digit isn't in 3456 is excluded even if Luhn-ok
    # (Luhn-valid, starts with 1): 1234 5678 9012 3452
    assert all(f["kind"] != "credit_card"
               for f in ss.scan("id 1234 5678 9012 3452"))


# ---------------------------------------------------------------------------
# helper boundaries
# ---------------------------------------------------------------------------

def test_value_is_secretish_boundaries():
    assert ss._value_is_secretish("a" * 12) is True         # length alone
    assert ss._value_is_secretish("s3cr3t!x") is True        # len 8, mixed
    assert ss._value_is_secretish("required") is False       # 8 alpha-only
    assert ss._value_is_secretish("short") is False          # < 8


def test_luhn_ok():
    assert ss._luhn_ok("4111111111111111") is True
    assert ss._luhn_ok("378282246310005") is True
    assert ss._luhn_ok("4111111111111112") is False

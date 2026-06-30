#!/usr/bin/env python3
"""
secret_scan.py — detect secret-shaped strings (spec §7, the write-side guard).

The mirror image of the resolver: resolvers READ secrets on demand; this module
makes sure secrets never get WRITTEN into the vault in the first place.

Two consumers, two strictness tiers:

  - ingest.py calls scan(text) over each raw document. It is BROAD on purpose:
    a hit downgrades the stub to `needs-review` (a human looks before the file
    is compiled/surfaced). Over-flagging here is cheap and safe.

  - validate.py calls looks_like_secret(value) over each frontmatter value. It
    is STRICT: only unambiguous, high-confidence secret formats, so the schema
    gate never false-fails on an ordinary title or id. A real key pasted into a
    field is a hard error.

CONTRACT: this module NEVER stores, returns, or logs the matched secret itself.
Findings carry a category and a short non-reversible fingerprint only.
"""
import re
import hashlib

__all__ = ["scan", "looks_like_secret", "redact", "Finding"]


# --- high-confidence formats (used by BOTH tiers) ---------------------------
# Each is specific enough that a match is almost certainly a real credential.
_STRICT = [
    ("private_key_block",
     re.compile(r"-----BEGIN (?:[A-Z0-9]+ )*PRIVATE KEY-----")),
    ("pgp_private_key",
     re.compile(r"-----BEGIN PGP PRIVATE KEY BLOCK-----")),
    ("aws_access_key_id",
     re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b")),
    ("github_token",
     re.compile(r"\bgh[pousr]_[A-Za-z0-9]{36,}\b")),
    ("slack_token",
     re.compile(r"\bxox[baprs]-[0-9A-Za-z]{8,}-[0-9A-Za-z-]{8,}\b")),
    ("google_api_key",
     re.compile(r"\bAIza[0-9A-Za-z_\-]{35}\b")),
    ("jwt",
     re.compile(r"\beyJ[A-Za-z0-9_\-]{6,}\.[A-Za-z0-9_\-]{6,}\.[A-Za-z0-9_\-]{6,}\b")),
    ("slack_webhook",
     re.compile(r"https://hooks\.slack\.com/services/[A-Za-z0-9/_\-]{20,}")),
]

# --- broad heuristics (ingest tier only) ------------------------------------
# `label = value` where the value itself looks non-trivial. Gated so dictionary
# words ("password: required") don't trip it, but "password: Hunter2!" does.
_LABELED = re.compile(
    r"(?i)\b(?:pass(?:word|phrase|wd)?|pwd|secret|api[_-]?key|access[_-]?token|"
    r"auth[_-]?token|client[_-]?secret|private[_-]?key)\b\s*[:=]\s*"
    r"['\"]?(?P<val>[^\s'\"]{6,})")

# Credit-card PAN in a conventional grouping, Luhn-validated. 4-4-4-4 covers
# Visa/MC/Discover; 4-6-5 covers Amex (which the digits[0] check downstream
# already implied but the old regex could never match). The grouping +
# checksum makes this far more card-specific than a bare digit run (so
# account numbers in a statement don't all light up).
_CARD = re.compile(r"\b(\d{4}[ -]\d{4}[ -]\d{4}[ -]\d{4}|\d{4}[ -]\d{6}[ -]\d{5})\b")


Finding = dict  # {"kind": str, "fingerprint": str}  — never the secret itself


def _fingerprint(secret_text):
    """Short, non-reversible tag so two sightings of the same secret dedupe
    without the value ever being retained."""
    return hashlib.sha256(secret_text.encode("utf-8", "replace")).hexdigest()[:8]


def _luhn_ok(digits):
    total, alt = 0, False
    for ch in reversed(digits):
        d = ord(ch) - 48
        if alt:
            d *= 2
            if d > 9:
                d -= 9
        total += d
        alt = not alt
    return total % 10 == 0


def _value_is_secretish(val):
    """A labeled value is only interesting if it doesn't look like a plain
    dictionary word: any 12+ char value qualifies, shorter ones need a
    letter-plus-(digit-or-symbol) mix at length >= 8. Cuts
    'password: required' while keeping 'password: s3cr3t!'."""
    if len(val) >= 12:
        return True
    has_alpha = bool(re.search(r"[A-Za-z]", val))
    has_other = bool(re.search(r"[\d\W]", val))
    return has_alpha and has_other and len(val) >= 8


def scan(text):
    """Broad scan for ingest. Returns a deduped list of Findings (kind +
    fingerprint), never the matched text. Empty list = nothing suspicious."""
    if not text:
        return []
    seen = set()
    out = []

    def add(kind, secret_text):
        fp = _fingerprint(secret_text)
        key = (kind, fp)
        if key not in seen:
            seen.add(key)
            out.append({"kind": kind, "fingerprint": fp})

    for kind, rx in _STRICT:
        for m in rx.finditer(text):
            add(kind, m.group(0))
    for m in _LABELED.finditer(text):
        val = m.group("val")
        if _value_is_secretish(val):
            add("labeled_secret", val)
    for m in _CARD.finditer(text):
        digits = re.sub(r"[ -]", "", m.group(1))
        if _luhn_ok(digits) and digits[0] in "3456":
            add("credit_card", digits)
    return out


def looks_like_secret(value):
    """Strict, single-value check for validate. Returns the secret KIND (str)
    if `value` is an unambiguous credential, else None. Deliberately excludes
    the broad label/card heuristics so the schema gate can't false-fail."""
    if not isinstance(value, str) or not value:
        return None
    for kind, rx in _STRICT:
        if rx.search(value):
            return kind
    return None


def redact(value):
    """Return `value` with any high-confidence secret substring replaced by a
    `[REDACTED:<kind>]` placeholder. Used defensively on a few Python-written
    fields (e.g. a title derived from a filename) so a secret can never ride
    into frontmatter even if it slipped through classification."""
    if not isinstance(value, str) or not value:
        return value
    out = value
    for kind, rx in _STRICT:
        out = rx.sub(f"[REDACTED:{kind}]", out)
    return out

#!/usr/bin/env python3
"""
lint.py — Stage 5. READ-ONLY anomaly report.

This is the monthly safety net. It surfaces problems that wouldn't break
validate.py but are signs of operational drift:

    - related: refs that point at a slug with no entity file
    - status: stub but prose body is non-empty (or vice versa)
    - status: compiled but sources_hash != compiled_from_hash (compile is behind)
    - needs-review entities that have been sitting > N days
    - proposals queued for review > N days
    - raw/ files not yet in _manifest.jsonl (ingest is behind)
    - manifest claims stubs that no longer exist on disk

Writes NOTHING (besides the optional report file). Cron-friendly: print
nothing if the vault is clean, print a structured summary if it isn't.

Usage:
    python -m agent_vault.lint [VAULT]              # human summary to stdout
    python -m agent_vault.lint [VAULT] --json       # JSON to stdout instead
    python -m agent_vault.lint [VAULT] --report PATH  # also write JSON to PATH
    python -m agent_vault.lint [VAULT] --aging-days 30  # override default 14-day aging
"""
import sys
import os
import re
import json
import datetime

# Force UTF-8 stdout/stderr so Unicode chars (→, —, box-drawing) don't crash
# on Windows consoles that default to cp1252.
for _s in (sys.stdout, sys.stderr):
    if hasattr(_s, "reconfigure"):
        _s.reconfigure(encoding="utf-8")

try:
    import yaml
except ImportError:
    sys.exit("pyyaml required:  pip install pyyaml --break-system-packages")


DEFAULT_AGING_DAYS = 14

FM_RE = re.compile(r"^---\n(.*?)\n---\n(.*)$", re.S)
LINKS_RE = re.compile(r"<!-- LINKS:BEGIN -->.*?<!-- LINKS:END -->", re.S)


# ============================================================================
# Vault walkers
# ============================================================================
def iter_entities(vault):
    """Yield (path, frontmatter_dict, body_after_links) for every entity file."""
    for d, _, files in os.walk(os.path.join(vault, "entities")):
        for fn in sorted(files):
            if not fn.endswith(".md"):
                continue
            path = os.path.join(d, fn)
            raw = open(path, encoding="utf-8").read()
            m = FM_RE.match(raw)
            if not m:
                continue
            try:
                fm = yaml.safe_load(m.group(1)) or {}
            except yaml.YAMLError:
                continue
            body = m.group(2)
            prose = LINKS_RE.sub("", body).strip()
            # Strip out comment-only lines so we don't false-positive on stubs
            # whose body has only template hints (none currently, but defensive).
            prose = re.sub(r"<!--.*?-->", "", prose, flags=re.S).strip()
            yield path, fm, prose


def entity_slugs(vault):
    """Set of 'type/slug' refs for every entity file on disk."""
    out = set()
    edir = os.path.join(vault, "entities")
    for d, _, files in os.walk(edir):
        for fn in files:
            if fn.endswith(".md"):
                t = os.path.relpath(d, edir).replace("\\", "/")
                out.add(f"{t}/{os.path.splitext(fn)[0]}")
    return out


def load_manifest(vault):
    p = os.path.join(vault, "raw", "_manifest.jsonl")
    if not os.path.exists(p):
        return []
    out = []
    for line in open(p, encoding="utf-8"):
        line = line.strip()
        if not line:
            continue
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            pass
    return out


def load_promoted(vault):
    p = os.path.join(vault, "discovery", "promoted.jsonl")
    if not os.path.exists(p):
        return []
    out = []
    for line in open(p, encoding="utf-8"):
        line = line.strip()
        if not line:
            continue
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            pass
    return out


def iter_raw_files(vault):
    raw = os.path.join(vault, "raw")
    for d, _, files in os.walk(raw):
        for fn in sorted(files):
            if fn.startswith("_") or fn == ".gitkeep":
                continue
            yield os.path.join(d, fn), os.path.relpath(os.path.join(d, fn), vault).replace("\\", "/")


# ============================================================================
# Checks — each returns a list of issue dicts
# ============================================================================
def check_broken_refs(vault, slugs):
    """`related:` entries that point at a slug with no entity file."""
    issues = []
    for path, fm, _ in iter_entities(vault):
        for r in (fm.get("related") or []):
            ref = str(r).strip()
            if "/" not in ref:
                issues.append({"kind": "broken_ref", "entity": fm.get("slug", "?"),
                               "path": _rel(path, vault), "ref": ref,
                               "reason": "ref missing 'type/' prefix"})
                continue
            if ref not in slugs:
                issues.append({"kind": "broken_ref", "entity": fm.get("slug", "?"),
                               "path": _rel(path, vault), "ref": ref,
                               "reason": "no entity file with that slug"})
    return issues


def check_status_prose_mismatch(vault):
    """status: stub should have empty prose; status: compiled should have prose."""
    issues = []
    for path, fm, prose in iter_entities(vault):
        status = fm.get("status")
        if status == "stub" and prose:
            issues.append({"kind": "stub_with_prose", "entity": fm.get("slug", "?"),
                           "path": _rel(path, vault),
                           "reason": "status=stub but prose body is non-empty"})
        elif status == "compiled" and not prose:
            issues.append({"kind": "compiled_without_prose", "entity": fm.get("slug", "?"),
                           "path": _rel(path, vault),
                           "reason": "status=compiled but prose body is empty"})
    return issues


def check_compile_drift(vault):
    """status: compiled but compiled_from_hash != sources_hash — recompile due."""
    issues = []
    for path, fm, _ in iter_entities(vault):
        if fm.get("status") != "compiled":
            continue
        sh = fm.get("sources_hash")
        cfh = fm.get("compiled_from_hash")
        if sh and cfh and sh != cfh:
            issues.append({"kind": "compile_drift", "entity": fm.get("slug", "?"),
                           "path": _rel(path, vault),
                           "sources_hash": sh, "compiled_from_hash": cfh,
                           "reason": "sources changed since last compile"})
    return issues


def check_aging_review(vault, aging_days):
    """needs-review entities that have been sitting too long."""
    issues = []
    today = datetime.date.today()
    for path, fm, _ in iter_entities(vault):
        if fm.get("status") != "needs-review":
            continue
        created = _parse_date(fm.get("created"))
        if not created:
            continue
        age = (today - created).days
        if age > aging_days:
            issues.append({"kind": "aging_needs_review", "entity": fm.get("slug", "?"),
                           "path": _rel(path, vault), "age_days": age,
                           "reason": f"needs-review for {age} days "
                                     f"(threshold {aging_days})"})
    return issues


def check_aging_queue(vault, aging_days):
    """proposals queued for review > aging_days ago and still queued."""
    issues = []
    today = datetime.date.today()
    promoted = load_promoted(vault)
    # Find the most recent decision per identity
    last = {}
    for r in promoted:
        last[tuple(r.get("identity") or ())] = r
    for ident, rec in last.items():
        if rec.get("action") != "queue_for_review":
            continue
        ts = rec.get("ts", "")
        try:
            d = datetime.datetime.fromisoformat(ts.replace("Z", "+00:00")).date()
        except (ValueError, AttributeError):
            continue
        age = (today - d).days
        if age > aging_days:
            issues.append({"kind": "aging_queue", "identity": "/".join(str(x) for x in ident),
                           "age_days": age,
                           "reason": f"queued for review {age} days ago "
                                     f"(threshold {aging_days})"})
    return issues


def check_stuck_reclassify(vault, aging_days):
    """Reclassify proposals whose most recent action is `reclassify_started`
    (the two-phase commit's intermediate state) but never reached a terminal
    state (`reclassify_applied` or `reclassify_abandoned`) within
    aging_days. Surfaces interrupted applies that didn't recover — usually
    a sign that the entity file disappeared or the operator killed the run
    between phases."""
    issues = []
    today = datetime.date.today()
    promoted = load_promoted(vault)
    last = {}
    for r in promoted:
        if r.get("kind") != "reclassify":
            continue
        last[tuple(r.get("identity") or ())] = r
    for ident, rec in last.items():
        if rec.get("action") != "reclassify_started":
            continue
        ts = rec.get("ts", "")
        try:
            d = datetime.datetime.fromisoformat(ts.replace("Z", "+00:00")).date()
        except (ValueError, AttributeError):
            continue
        age = (today - d).days
        if age > aging_days:
            issues.append({"kind": "stuck_reclassify",
                           "identity": "/".join(str(x) for x in ident),
                           "age_days": age,
                           "reason": f"reclassify_started {age} days ago, "
                                     f"never reached applied/abandoned "
                                     f"(threshold {aging_days})"})
    return issues


def check_manifest_drift(vault):
    """raw/ files not in the manifest (= ingest hasn't seen them yet)."""
    issues = []
    manifest = load_manifest(vault)
    manifest_paths = {r.get("path") for r in manifest}
    for _abspath, rel in iter_raw_files(vault):
        if rel not in manifest_paths:
            issues.append({"kind": "raw_not_ingested", "path": rel,
                           "reason": "file in raw/ has not been processed by ingest.py"})
    return issues


def check_manifest_orphan_stubs(vault):
    """manifest claims stubs that no longer exist on disk (something deleted them)."""
    issues = []
    manifest = load_manifest(vault)
    slugs = entity_slugs(vault)
    for rec in manifest:
        for stub_slug in (rec.get("stubs") or []):
            matches = [s for s in slugs if s.endswith("/" + stub_slug)]
            if not matches:
                issues.append({"kind": "manifest_orphan", "raw_path": rec.get("path"),
                               "missing_stub": stub_slug,
                               "reason": "manifest references a stub no entity file"})
    return issues


def check_ingest_errors(vault):
    """raw/ files that ingest skipped (poison files that raised, or oversized
    files). Recorded in the manifest with an `error` field so the operator can
    see them rather than discovering a file silently never got ingested."""
    issues = []
    for rec in load_manifest(vault):
        if rec.get("error"):
            issues.append({"kind": "ingest_error", "path": rec.get("path"),
                           "reason": rec["error"]})
    return issues


# ============================================================================
# Driver
# ============================================================================
def lint(vault, aging_days=DEFAULT_AGING_DAYS):
    slugs = entity_slugs(vault)
    checks = [
        ("broken_refs",            lambda: check_broken_refs(vault, slugs)),
        ("status_prose_mismatch",  lambda: check_status_prose_mismatch(vault)),
        ("compile_drift",          lambda: check_compile_drift(vault)),
        ("aging_needs_review",     lambda: check_aging_review(vault, aging_days)),
        ("aging_queue",            lambda: check_aging_queue(vault, aging_days)),
        ("stuck_reclassify",       lambda: check_stuck_reclassify(vault, aging_days)),
        ("raw_not_ingested",       lambda: check_manifest_drift(vault)),
        ("manifest_orphan_stubs",  lambda: check_manifest_orphan_stubs(vault)),
        ("ingest_errors",          lambda: check_ingest_errors(vault)),
    ]
    report = {
        "ts": datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds"),
        "vault": os.path.abspath(vault),
        "aging_days": aging_days,
        "issues_by_check": {},
        "total_issues": 0,
    }
    for name, fn in checks:
        issues = fn()
        report["issues_by_check"][name] = issues
        report["total_issues"] += len(issues)
    return report


def _rel(path, vault):
    return os.path.relpath(path, vault).replace("\\", "/")


def _parse_date(s):
    if not s:
        return None
    try:
        return datetime.date.fromisoformat(str(s))
    except (ValueError, TypeError):
        return None


def format_human(report):
    if report["total_issues"] == 0:
        return ""  # cron-friendly silence on clean runs
    lines = [f"# Agent Vault lint report  ({report['ts']})",
             f"vault: {report['vault']}",
             f"aging threshold: {report['aging_days']} days",
             ""]
    for name, issues in report["issues_by_check"].items():
        if not issues:
            continue
        lines.append(f"## {name}  ({len(issues)})")
        for i in issues:
            primary = i.get("entity") or i.get("identity") or i.get("path") or "?"
            detail = i.get("ref") or i.get("missing_stub") or ""
            row = f"  - {primary}"
            if detail:
                row += f"  →  {detail}"
            row += f"   ({i.get('reason', '')})"
            lines.append(row)
        lines.append("")
    lines.append(f"total issues: {report['total_issues']}")
    return "\n".join(lines)


def main():
    args = sys.argv[1:]
    vault = "."
    aging = DEFAULT_AGING_DAYS
    as_json = False
    report_path = None
    i = 0
    while i < len(args):
        a = args[i]
        if a in ("-h", "--help"):
            print(__doc__); return 0
        elif a == "--json":
            as_json = True
        elif a == "--report" and i + 1 < len(args):
            report_path = args[i + 1]; i += 2; continue
        elif a == "--aging-days" and i + 1 < len(args):
            aging = int(args[i + 1]); i += 2; continue
        elif not a.startswith("-"):
            vault = a
        i += 1

    report = lint(vault, aging_days=aging)
    if report_path:
        os.makedirs(os.path.dirname(report_path) or ".", exist_ok=True)
        with open(report_path, "w", encoding="utf-8") as f:
            json.dump(report, f, indent=2)

    if as_json:
        json.dump(report, sys.stdout, indent=2)
        sys.stdout.write("\n")
    else:
        text = format_human(report)
        if text:
            print(text)

    # Exit 1 if any issues so cron / systemd can wake a human.
    return 1 if report["total_issues"] else 0


if __name__ == "__main__":
    sys.exit(main())

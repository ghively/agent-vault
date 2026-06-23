#!/usr/bin/env python3
"""
review.py — the human approve/reject mechanism for everything the pipeline
queues for review. Closes the loop the spec calls for in §5.2: "a review
queue Python writes for the human ... + an approve/reject mechanism."

Two queues, one tool:

  PROPOSALS (registry vocabulary, queued by promote.py):
      list                      pending queued proposals, each with a short id
      show <id>                 full detail for one
      approve <id> [--reason]   write the registry (same deterministic
                                apply_* writers promote.py uses) + audit log
      reject <id> [--reason]    park it; stands until NEW evidence arrives
                                (promote re-queues only if sightings grow)

  ENTITIES (needs-review pages: human_gated types, low-confidence stubs,
  inferred auto-stubs):
      entities                  list every needs-review entity (gated flagged)
      approve-entity <type/slug>   flip to `stub` (compile picks it up next
                                   pass); a page that already has prose flips
                                   to `compiled` with hashes stamped so the
                                   compile pass leaves the human prose alone
      reject-entity <type/slug> [--reason]   flip to `archived`

Every decision is an append-only record in discovery/promoted.jsonl
(`review_approved` / `review_rejected` / `entity_approved` /
`entity_rejected`) — the same ledger promote.py and reclassify_apply.py
read, so all three stay consistent:
  - promote.py won't re-queue a review_rejected concept at the same
    sighting count, and won't re-log a concept a human already approved;
  - reclassify_apply.py treats review_rejected as blocking (until promote
    re-queues on new evidence).

Reclassify approvals delegate to reclassify_apply.apply_all (the existing
two-phase-commit apply step) rather than duplicating its recovery logic.

Ownership stays intact: a human DECIDES here; deterministic code writes.

Usage:
  python3 review.py [VAULT] <command> [args]
"""
import sys, os, re, json, hashlib, datetime

# Force UTF-8 stdout/stderr so entity titles with Unicode don't crash on
# Windows consoles that default to cp1252.
for _s in (sys.stdout, sys.stderr):
    if hasattr(_s, "reconfigure"):
        _s.reconfigure(encoding="utf-8")

try:
    import yaml
except ImportError:
    sys.exit("pyyaml required:  pip install pyyaml --break-system-packages")

try:
    from locking import vault_lock
except Exception:
    from contextlib import nullcontext
    def vault_lock(_v):
        return nullcontext()

PARSE_RE = re.compile(r"^---\n(.*?)\n---\n(.*)$", re.S)
LINKS_RE = re.compile(r"<!-- LINKS:BEGIN -->.*?<!-- LINKS:END -->", re.S)
STATUS_LINE_RE = re.compile(r"^(status:[ \t]*)\S+[^\n]*$", re.M)
COMPILED_FROM_LINE_RE = re.compile(r"^compiled_from_hash:[ \t]*[^\n]*$", re.M)


def _now():
    return datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")


def load_jsonl(path):
    if not os.path.exists(path):
        return []
    out = []
    for line in open(path, encoding="utf-8"):
        line = line.strip()
        if not line:
            continue
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            pass
    return out


def append_log(vault, record):
    p = os.path.join(vault, "discovery", "promoted.jsonl")
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, "a", encoding="utf-8") as f:
        f.write(json.dumps(record) + "\n")
        f.flush(); os.fsync(f.fileno())


# ============================================================================
# Proposal queue
# ============================================================================
def short_id(identity):
    """Stable 8-hex id for an identity tuple/list — what the human types."""
    return hashlib.sha256(json.dumps(list(identity)).encode()).hexdigest()[:8]


def pending_proposals(vault):
    """Most-recent-record-wins over promoted.jsonl: an identity is pending
    iff its latest action is `queue_for_review`. Approvals, rejections,
    applied/abandoned reclassifies and auto-promotions all close it (a later
    re-queue by promote — new evidence — re-opens it)."""
    log = load_jsonl(os.path.join(vault, "discovery", "promoted.jsonl"))
    latest = {}
    for rec in log:
        ident = tuple(rec.get("identity") or ())
        if ident:
            latest[ident] = rec
    out = []
    for ident, rec in sorted(latest.items(), key=lambda kv: str(kv[0])):
        if rec.get("action") == "queue_for_review":
            out.append((short_id(ident), ident, rec))
    return out


def find_pending(vault, sid):
    matches = [(i, r) for s, i, r in pending_proposals(vault) if s == sid]
    if not matches:
        sys.exit(f"no pending proposal with id {sid!r} — run `list` to see the queue")
    return matches[0]


def cmd_list(vault):
    pend = pending_proposals(vault)
    if not pend:
        print("review queue: empty")
        return 0
    print(f"review queue: {len(pend)} pending proposal(s)\n")
    print(f"  {'id':8}  {'kind':10}  {'concept':36}  {'sight':>5}  reason")
    for sid, ident, rec in pend:
        concept = "/".join(str(x) for x in ident[1:]) or str(ident)
        reason = (rec.get("reason") or "")[:60]
        print(f"  {sid}  {rec.get('kind',''):10}  {concept:36}  "
              f"{rec.get('sightings', 0):>5}  {reason}")
    print("\n  approve with:  python3 review.py . approve <id>")
    return 0


def cmd_show(vault, sid):
    ident, rec = find_pending(vault, sid)
    print(json.dumps(rec, indent=2))
    return 0


def cmd_approve(vault, sid, reason=""):
    ident, rec = find_pending(vault, sid)
    kind = rec.get("kind")
    p = rec.get("proposal") or {}
    today = datetime.date.today().isoformat()

    sys.path.insert(0, vault)
    try:
        import promote
    finally:
        sys.path.pop(0)

    note = reason or "approved via review.py"
    if kind == "reclassify":
        # Delegate to the existing two-phase apply step (it takes the vault
        # lock itself — don't nest). The review_approved record is appended
        # first so the audit trail shows the human decision even if the
        # apply is interrupted (it resumes idempotently on re-run).
        slug = p.get("from_entity") or (ident[1] if len(ident) > 1 else None)
        if not slug:
            sys.exit(f"reclassify proposal {sid} has no source slug")
        _log_review(vault, ident, rec, "review_approved", note)
        sys.path.insert(0, vault)
        try:
            import reclassify_apply
        finally:
            sys.path.pop(0)
        results = reclassify_apply.apply_all(vault, only_slug=slug)
        n_applied = sum(1 for r in results if r.get("status") == "applied")
        print(f"approved {sid}: reclassify {slug} -> "
              f"{p.get('to_type')}/{p.get('to_subtype')} "
              f"(applied={n_applied})")
        return 0

    with vault_lock(vault):
        if kind == "tag":
            applied = promote.apply_tag(vault, p["name"],
                                        {"sightings": rec.get("sightings", 0)},
                                        today)
            target = f"tag '{p['name']}'"
        elif kind == "alias":
            applied = promote.apply_alias(vault, p["from"], p["to"])
            target = f"alias '{p['from']}' -> '{p['to']}'"
        elif kind == "subtype":
            applied = promote.apply_subtype(vault, p["type"], p["name"])
            target = f"subtype '{p['type']}/{p['name']}'"
        elif kind == "type":
            applied = promote.apply_type(vault, p["name"], today)
            target = f"type '{p['name']}'"
        else:
            sys.exit(f"don't know how to apply kind {kind!r}")
        _log_review(vault, ident, rec, "review_approved",
                    note if applied else f"{note} (already in registry)")
    print(f"approved {sid}: {target}"
          + ("" if applied else "  (registry already had it)"))
    if kind == "type":
        print(f"  -> refine the new type's description/subtypes in "
              f"registry/schema.yaml (seeded with subtypes: [general])")
    return 0


def cmd_reject(vault, sid, reason=""):
    ident, rec = find_pending(vault, sid)
    _log_review(vault, ident, rec, "review_rejected",
                reason or "rejected via review.py")
    print(f"rejected {sid} ({rec.get('kind')}): stays rejected unless new "
          f"evidence arrives (promote re-queues only if sightings grow)")
    return 0


def _log_review(vault, ident, queued_rec, action, reason):
    append_log(vault, {
        "ts": _now(),
        "identity": list(ident),
        "kind": queued_rec.get("kind"),
        "action": action,
        "reason": reason,
        # Carried so promote.py's "rejection stands at same sighting count"
        # comparison works against this record.
        "sightings": queued_rec.get("sightings", 0),
        "proposal": queued_rec.get("proposal"),
        "by": "review.py",
    })


# ============================================================================
# Entity queue (needs-review pages)
# ============================================================================
def _gated_types(vault):
    try:
        schema = yaml.safe_load(open(os.path.join(vault, "registry", "schema.yaml"),
                                     encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError):
        return set()
    return {t for t, spec in (schema.get("types") or {}).items()
            if isinstance(spec, dict) and spec.get("human_gated")}


def needs_review_entities(vault):
    gated = _gated_types(vault)
    out = []
    edir = os.path.join(vault, "entities")
    for d, _, files in os.walk(edir):
        for fn in sorted(files):
            if not fn.endswith(".md"):
                continue
            path = os.path.join(d, fn)
            m = PARSE_RE.match(open(path, encoding="utf-8").read())
            if not m:
                continue
            try:
                fm = yaml.safe_load(m.group(1)) or {}
            except yaml.YAMLError:
                continue
            if fm.get("status") != "needs-review":
                continue
            t = os.path.relpath(d, edir).replace("\\", "/")
            out.append({"ref": f"{t}/{os.path.splitext(fn)[0]}",
                        "path": path, "fm": fm, "gated": t in gated})
    return out


def cmd_entities(vault):
    ents = needs_review_entities(vault)
    if not ents:
        print("entity queue: no needs-review entities")
        return 0
    print(f"entity queue: {len(ents)} needs-review entit{'y' if len(ents)==1 else 'ies'}\n")
    for e in ents:
        fm = e["fm"]
        flags = []
        if e["gated"]:
            flags.append("GATED")
        if fm.get("inferred"):
            flags.append("inferred")
        flag_s = f"  [{','.join(flags)}]" if flags else ""
        print(f"  {e['ref']:48}  conf={fm.get('confidence', '?')}  "
              f"created={fm.get('created','?')}{flag_s}")
        print(f"      {fm.get('title','')}")
    print("\n  approve with:  python3 review.py . approve-entity <type/slug>")
    return 0


def _entity_path(vault, ref):
    if "/" not in ref:
        sys.exit(f"entity ref must be type/slug, got {ref!r}")
    t, slug = ref.split("/", 1)
    path = os.path.join(vault, "entities", t, f"{slug}.md")
    if not os.path.exists(path):
        sys.exit(f"no entity at {path}")
    return path


def _set_entity_status(path, new_status, stamp_compiled_hash=False):
    """Targeted `status:` line edit (and optionally a compiled_from_hash
    stamp) — every other byte of the file, including prose, is untouched."""
    raw = open(path, encoding="utf-8").read()
    m = PARSE_RE.match(raw)
    if not m:
        sys.exit(f"{path}: no frontmatter block")
    fm_text, body = m.group(1), m.group(2)
    if not STATUS_LINE_RE.search(fm_text):
        sys.exit(f"{path}: no `status:` line in frontmatter")
    new_fm = STATUS_LINE_RE.sub(rf"\g<1>{new_status}", fm_text, count=1)
    if stamp_compiled_hash:
        fm = yaml.safe_load(fm_text) or {}
        sh = str(fm.get("sources_hash", ""))
        if COMPILED_FROM_LINE_RE.search(new_fm):
            new_fm = COMPILED_FROM_LINE_RE.sub(
                f"compiled_from_hash: {sh}", new_fm, count=1)
        else:
            new_fm = new_fm.rstrip() + f"\ncompiled_from_hash: {sh}"
    new_text = f"---\n{new_fm}\n---\n{body}"
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(new_text)
        f.flush(); os.fsync(f.fileno())
    os.replace(tmp, path)


def cmd_approve_entity(vault, ref, reason=""):
    path = _entity_path(vault, ref)
    with vault_lock(vault):
        raw = open(path, encoding="utf-8").read()
        m = PARSE_RE.match(raw)
        fm = yaml.safe_load(m.group(1)) if m else {}
        if (fm or {}).get("status") != "needs-review":
            sys.exit(f"{ref}: status is {(fm or {}).get('status')!r}, "
                     f"not needs-review — nothing to approve")
        prose = LINKS_RE.sub("", m.group(2)).strip()
        if prose:
            # Human-authored prose already present: approving means "this
            # page is done" — stamp compiled + matching hash so the compile
            # pass doesn't clobber it (same posture as seed entities).
            _set_entity_status(path, "compiled", stamp_compiled_hash=True)
            new_status = "compiled"
        else:
            _set_entity_status(path, "stub")
            new_status = "stub"
        append_log(vault, {
            "ts": _now(), "identity": ["entity", ref], "kind": "entity",
            "action": "entity_approved", "reason": reason or "approved via review.py",
            "new_status": new_status, "by": "review.py",
        })
    print(f"approved {ref}: status -> {new_status}"
          + ("" if new_status == "compiled"
             else "  (next compile pass writes its prose)"))
    return 0


def cmd_reject_entity(vault, ref, reason=""):
    path = _entity_path(vault, ref)
    with vault_lock(vault):
        raw = open(path, encoding="utf-8").read()
        m = PARSE_RE.match(raw)
        fm = yaml.safe_load(m.group(1)) if m else {}
        if (fm or {}).get("status") != "needs-review":
            sys.exit(f"{ref}: status is {(fm or {}).get('status')!r}, "
                     f"not needs-review — nothing to reject")
        _set_entity_status(path, "archived")
        append_log(vault, {
            "ts": _now(), "identity": ["entity", ref], "kind": "entity",
            "action": "entity_rejected", "reason": reason or "rejected via review.py",
            "by": "review.py",
        })
    print(f"rejected {ref}: status -> archived (file kept; delete by hand "
          f"if it's junk)")
    return 0


# ============================================================================
# CLI
# ============================================================================
def main():
    args = sys.argv[1:]
    if not args or args[0] in ("-h", "--help"):
        print(__doc__)
        return 0
    vault = "."
    if os.path.isdir(args[0]) or args[0] in (".", ".."):
        vault, args = args[0], args[1:]
    if not args:
        print(__doc__)
        return 2
    cmd, rest = args[0], args[1:]

    reason = ""
    if "--reason" in rest:
        i = rest.index("--reason")
        if i + 1 < len(rest):
            reason = rest[i + 1]
            rest = rest[:i] + rest[i + 2:]

    if cmd == "list":
        return cmd_list(vault)
    if cmd == "show" and rest:
        return cmd_show(vault, rest[0])
    if cmd == "approve" and rest:
        return cmd_approve(vault, rest[0], reason)
    if cmd == "reject" and rest:
        return cmd_reject(vault, rest[0], reason)
    if cmd == "entities":
        return cmd_entities(vault)
    if cmd == "approve-entity" and rest:
        return cmd_approve_entity(vault, rest[0], reason)
    if cmd == "reject-entity" and rest:
        return cmd_reject_entity(vault, rest[0], reason)
    print(__doc__)
    return 2


if __name__ == "__main__":
    sys.exit(main())

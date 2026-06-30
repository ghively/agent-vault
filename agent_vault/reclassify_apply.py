#!/usr/bin/env python3
"""
reclassify_apply.py — Apply queued reclassify proposals to the vault.

Stage 4 (promote.py) queues every reclassify proposal for human review
instead of auto-applying it: reclassification touches entity files, not
the registry, and a bad move can scatter cross-refs across the vault.
This script is the deliberate apply step — explicit, idempotent, and
re-run-safe.

What it does for each queued reclassify in discovery/promoted.jsonl:

  1. Locate the source entity by slug.
  2. Validate (to_type, to_subtype) exists in registry/schema.yaml.
  3. If type changes: rename entities/<old_type>/<slug>.md to
                      entities/<new_type>/<slug>.md (os.replace).
     If only subtype changes: leave the file in place.
  4. Rewrite the entity's frontmatter `type:` and `subtype:`.
  5. Walk every OTHER entity; rewrite any `related: [old_type/slug]` ref
     to `new_type/slug` and rebuild its LINKS:BEGIN/END block.
  6. Append a `reclassify_applied` record to promoted.jsonl so a re-run
     is a no-op.

Usage:
  python3 reclassify_apply.py [VAULT]              # apply all queued
  python3 reclassify_apply.py [VAULT] --slug X     # apply only entity X
  python3 reclassify_apply.py [VAULT] --dry-run    # show plan, write nothing
"""
import sys
import os
import re
import json
import datetime

try:
    import yaml
except ImportError:
    sys.exit("pyyaml required:  pip install pyyaml --break-system-packages")

# vault-wide write lock (overlapping mutating runs serialize; readers don't lock)
try:
    from .locking import vault_lock
except Exception:
    from contextlib import nullcontext
    def vault_lock(_v):
        return nullcontext()


PARSE_RE = re.compile(r"^---\n(.*?)\n---\n(.*)$", re.S)
LINKS_RE = re.compile(r"<!-- LINKS:BEGIN -->.*?<!-- LINKS:END -->", re.S)


def load_jsonl(path):
    if not os.path.exists(path):
        return []
    out = []
    for line in open(path, encoding="utf-8"):
        line = line.strip()
        if not line: continue
        try: out.append(json.loads(line))
        except json.JSONDecodeError: pass
    return out


def queued_reclassifies(promoted_log):
    """Identities whose LATEST ledger state says they are pending: the most
    recent action is `queue_for_review` (or `reclassify_started`, i.e. a
    previous apply was interrupted mid-flight — surfaced with its record so
    the resume path can trust the originally captured old_type).

    Last-action-wins matters: `reclassify_applied` / `reclassify_abandoned`
    close an identity, and a human `review_rejected` (review.py) blocks it —
    but a LATER `queue_for_review` (promote re-queues when new evidence
    arrives) re-opens it. `review_approved` is an approval awaiting apply
    (review.py logs it BEFORE delegating here), so it stays pending."""
    queued = {}     # ident -> most recent queue_for_review record
    last = {}       # ident -> most recent action of any kind
    started = {}    # ident -> most recent reclassify_started record
    for rec in promoted_log:
        if rec.get("kind") != "reclassify":
            continue
        action = rec.get("action")
        ident = tuple(rec.get("identity") or ())
        last[ident] = action
        if action == "queue_for_review":
            queued[ident] = rec
        elif action == "reclassify_started":
            started[ident] = rec
    out = []
    for ident, rec in queued.items():
        if last.get(ident) not in ("queue_for_review", "reclassify_started",
                                   "review_approved"):
            continue
        out.append((ident, rec, started.get(ident)))
    return out


def find_entity_by_slug(vault, slug):
    """Return (type, path) for the entity with this slug, or (None, None).
    Walks every type dir and collects matches: a slug appearing under two
    types is an integrity bug, not something we silently first-wins through."""
    edir = os.path.join(vault, "entities")
    matches = []
    for d, _, files in os.walk(edir):
        for fn in files:
            if fn == f"{slug}.md":
                t = os.path.relpath(d, edir)
                matches.append((t, os.path.join(d, fn)))
    if len(matches) > 1:
        types = ", ".join(t for t, _ in matches)
        raise RuntimeError(
            f"slug {slug!r} exists under multiple type dirs ({types}); "
            f"refusing to reclassify ambiguously — resolve by hand first"
        )
    return matches[0] if matches else (None, None)


def schema_subtypes(vault, type_):
    schema = yaml.safe_load(open(os.path.join(vault, "registry", "schema.yaml"),
                                 encoding="utf-8")) or {}
    types = schema.get("types") or {}
    if type_ not in types:
        return None
    return set(types[type_].get("subtypes") or [])


def rewrite_entity(path, new_type, new_subtype, dry_run=False):
    """Rewrite the type:/subtype: lines in an entity's frontmatter in place.
    Constrains the regex to the frontmatter block so prose can't be touched
    even if it accidentally contains a 'type:' line."""
    raw = open(path, encoding="utf-8").read()
    m = PARSE_RE.match(raw)
    if not m:
        return False
    fm_text, body = m.group(1), m.group(2)
    new_fm = re.sub(r"^type:\s*.+$",    f"type: {new_type}",    fm_text,
                    count=1, flags=re.M)
    new_fm = re.sub(r"^subtype:\s*.+$", f"subtype: {new_subtype}", new_fm,
                    count=1, flags=re.M)
    if new_fm == fm_text:
        return False
    new_text = f"---\n{new_fm}\n---\n{body}"
    if not dry_run:
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            f.write(new_text)
            f.flush(); os.fsync(f.fileno())
        os.replace(tmp, path)
    return True


def rewrite_refs(vault, old_ref, new_ref, dry_run=False):
    """For every entity that has `related: [old_ref]`, rewrite that ref in
    the parsed frontmatter list (NOT a raw substring replace — prose stays
    inviolate; this script must not touch text the LLM owns) and regenerate
    the LINKS:BEGIN/END block. Returns count of files actually modified."""
    if old_ref == new_ref:
        return 0
    count = 0
    edir = os.path.join(vault, "entities")
    for d, _, files in os.walk(edir):
        for fn in files:
            if not fn.endswith(".md"):
                continue
            path = os.path.join(d, fn)
            raw = open(path, encoding="utf-8").read()
            m = PARSE_RE.match(raw)
            if not m:
                continue
            try: fm = yaml.safe_load(m.group(1)) or {}
            except yaml.YAMLError: continue
            related = [str(r) for r in (fm.get("related") or [])]
            if old_ref not in related:
                continue
            new_related = [new_ref if r == old_ref else r for r in related]
            # Frontmatter edit: rewrite ONLY the `related:` list block — find
            # the `  - {old_ref}` line that immediately follows `related:`.
            fm_text = m.group(1)
            new_fm_text = _rewrite_related_in_fm(fm_text, old_ref, new_ref)
            if new_fm_text == fm_text:
                continue  # could not locate the line safely; skip
            body = m.group(2)
            inner = "\n".join(f"- Related: [[{r}]]" for r in new_related)
            if inner:
                inner += "\n"
            new_block = f"<!-- LINKS:BEGIN -->\n{inner}<!-- LINKS:END -->"
            new_body = LINKS_RE.sub(new_block, body, count=1)
            new_text = f"---\n{new_fm_text}\n---\n{new_body}"
            if new_text == raw:
                continue
            if not dry_run:
                tmp = path + ".tmp"
                with open(tmp, "w", encoding="utf-8") as f:
                    f.write(new_text)
                    f.flush(); os.fsync(f.fileno())
                os.replace(tmp, path)
            count += 1
    return count


def _rewrite_related_in_fm(fm_text, old_ref, new_ref):
    """Rewrite occurrences of `old_ref` inside the `related:` field of
    frontmatter text. Handles both block style:

        related:
          - media/foo
          - other/bar

    and flow style on a single line:

        related: [media/foo, other/bar]

    Returns unchanged input if the ref can't be found in `related:`.
    Other YAML lists in frontmatter (sources:, tags:) are untouched even
    if they coincidentally contain the same string."""
    lines = fm_text.split("\n")

    # Flow-style first: `related: [...]` on one line.
    for i, line in enumerate(lines):
        stripped = line.lstrip()
        if not stripped.startswith("related:"):
            continue
        # Flow style is detected by a `[` after the colon.
        after = stripped[len("related:"):].lstrip()
        if after.startswith("["):
            # Split at the colon, rewrite ONLY inside the flow list portion.
            head, _, list_text = line.partition("[")
            list_text = "[" + list_text  # restore
            # Surgical: replace whole-token occurrences of old_ref inside the
            # bracketed list — bounded by [ , or ].
            new_list_text = re.sub(
                r"(?<=[\[,\s])" + re.escape(old_ref) + r"(?=[\],\s])",
                new_ref, list_text)
            if new_list_text != list_text:
                lines[i] = head + new_list_text
                return "\n".join(lines)
            return fm_text
        else:
            # Block style: search subsequent indented `- ref` lines.
            for j in range(i + 1, len(lines)):
                raw_line = lines[j]
                s = raw_line.rstrip()
                if s.strip() == "":
                    return fm_text  # blank line ends the block
                if s and not s.startswith(" ") and not s.startswith("-"):
                    return fm_text  # dedented top-level key ends the block
                if s.lstrip().startswith("- ") and s.lstrip()[2:].strip() == old_ref:
                    lines[j] = raw_line.replace(old_ref, new_ref, 1)
                    return "\n".join(lines)
            return fm_text
    return fm_text


def _append_log(vault, record):
    p = os.path.join(vault, "discovery", "promoted.jsonl")
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, "a", encoding="utf-8") as f:
        f.write(json.dumps(record) + "\n")


def _now():
    return datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")


def apply_reclassify(vault, slug, to_type, to_subtype, identity,
                     proposal, started_rec=None, dry_run=False):
    """Apply one reclassify in idempotent, recoverable order:

        1. Locate the entity. If `started_rec` is provided (mid-flight
           recovery from a previous interrupted run), trust its `old_type`
           snapshot instead of re-deriving from the directory.
        2. Log `reclassify_started` BEFORE any mutation, carrying the
           old_type snapshot. If we crash now, re-runs can recover.
        3. Rewrite the `related:` ref in every OTHER entity (idempotent
           per entity; ops that already happened are no-ops).
        4. Rewrite this entity's `type:`/`subtype:` frontmatter at its
           current path. If the file is still at the old path, mutate there;
           if a previous run already moved it, mutate at the new path.
        5. Move the file via os.replace if type changed and file is still
           at the old path.
        6. Log `reclassify_applied`."""
    if started_rec:
        snap_old_type = started_rec.get("old_type")
        # Trust the snapshot. The entity might be at old or new path.
        old_path_guess = os.path.join(vault, "entities", snap_old_type, f"{slug}.md")
        new_path_guess = os.path.join(vault, "entities", to_type, f"{slug}.md")
        if os.path.exists(old_path_guess):
            old_type, old_path = snap_old_type, old_path_guess
        elif os.path.exists(new_path_guess):
            old_type, old_path = snap_old_type, new_path_guess  # already moved
        else:
            # Escape hatch: the entity was deleted (by hand or by another
            # process) between `started` and now. Without this, every future
            # run skips this identity in perpetuity. Log a terminal
            # `reclassify_abandoned` action so the queue can finally move on.
            if not dry_run:
                _append_log(vault, {
                    "ts": _now(), "identity": list(identity),
                    "kind": "reclassify",
                    "action": "reclassify_abandoned",
                    "reason": f"slug {slug!r} no longer exists at either "
                              f"old ({snap_old_type}) or new ({to_type}) path; "
                              f"giving up",
                    "proposal": proposal,
                })
            return {"slug": slug, "status": "abandoned",
                    "reason": "entity file gone; logged reclassify_abandoned"}
    else:
        try:
            old_type, old_path = find_entity_by_slug(vault, slug)
        except RuntimeError as e:
            return {"slug": slug, "status": "skip", "reason": str(e)}
        if not old_path:
            return {"slug": slug, "status": "skip",
                    "reason": f"no entity with slug {slug!r}"}

    valid_subs = schema_subtypes(vault, to_type)
    if valid_subs is None:
        return {"slug": slug, "status": "skip",
                "reason": f"target type {to_type!r} not in schema"}
    if to_subtype not in valid_subs:
        return {"slug": slug, "status": "skip",
                "reason": f"target subtype {to_subtype!r} not valid for "
                          f"type {to_type!r} (valid: {sorted(valid_subs)})"}

    plan = {"slug": slug, "from": f"{old_type}/{slug}",
            "to": f"{to_type}/{slug}", "moves_file": old_type != to_type}

    if dry_run:
        # Count what WOULD be rewritten without touching anything.
        refs_updated = sum(1 for _, _, _ in _iter_entities_with_ref(
            vault, f"{old_type}/{slug}"))
        plan.update(status="applied", refs_updated=refs_updated)
        return plan

    # Step 2: log started snapshot BEFORE any mutation (idempotent: skip if
    # we're resuming from one we already wrote).
    if not started_rec:
        _append_log(vault, {
            "ts": _now(), "identity": list(identity), "kind": "reclassify",
            "action": "reclassify_started",
            "reason": f"begin {plan['from']} -> {plan['to']}",
            "old_type": old_type, "to_type": to_type, "to_subtype": to_subtype,
            "proposal": proposal,
        })

    # Step 3: rewrite refs in OTHER entities (idempotent — only acts on
    # entities that still carry the OLD ref).
    refs_updated = rewrite_refs(vault, f"{old_type}/{slug}",
                                f"{to_type}/{slug}", dry_run=False)

    # Step 4: rewrite this entity's frontmatter. Mutate at whichever path
    # it currently lives at (old or new — both can happen during recovery).
    current_path = old_path if os.path.exists(old_path) else \
        os.path.join(vault, "entities", to_type, f"{slug}.md")
    rewrite_entity(current_path, to_type, to_subtype, dry_run=False)

    # Step 5: file move (if type changed AND not already moved).
    if old_type != to_type:
        new_dir = os.path.join(vault, "entities", to_type)
        new_path = os.path.join(new_dir, f"{slug}.md")
        if current_path != new_path:
            os.makedirs(new_dir, exist_ok=True)
            os.replace(current_path, new_path)

    # Step 6: log applied.
    _append_log(vault, {
        "ts": _now(), "identity": list(identity), "kind": "reclassify",
        "action": "reclassify_applied",
        "reason": f"moved {plan['from']} -> {plan['to']}",
        "from_path": plan["from"], "to_path": plan["to"],
        "refs_updated": refs_updated, "proposal": proposal,
    })
    plan.update(status="applied", refs_updated=refs_updated)
    return plan


def _iter_entities_with_ref(vault, ref):
    """Yield (path, fm, related) for every entity that has `ref` in `related:`."""
    edir = os.path.join(vault, "entities")
    for d, _, files in os.walk(edir):
        for fn in files:
            if not fn.endswith(".md"): continue
            path = os.path.join(d, fn)
            raw = open(path, encoding="utf-8").read()
            m = PARSE_RE.match(raw)
            if not m: continue
            try: fm = yaml.safe_load(m.group(1)) or {}
            except yaml.YAMLError: continue
            related = [str(r) for r in (fm.get("related") or [])]
            if ref in related:
                yield path, fm, related


def apply_all(vault, only_slug=None, dry_run=False):
    with vault_lock(vault):
        out = _apply_all_locked(vault, only_slug, dry_run)
        # Moving entity files invalidates _index.json; rebuild it inside the lock
        # so `synapse show`/`resolve` don't break for relocated entities.
        if not dry_run and any(r.get("status") == "applied" for r in out):
            _refresh_index(vault)
    return out


def _refresh_index(vault):
    try:
        sys.path.insert(0, vault)
        from . import build_index
        saved = sys.argv[:]
        sys.argv = ["build_index.py", vault]
        try:
            build_index.main()
        finally:
            sys.argv = saved
    except Exception as e:
        print(f"warn: index rebuild after reclassify failed: {e}", file=sys.stderr)


def _apply_all_locked(vault, only_slug=None, dry_run=False):
    log = load_jsonl(os.path.join(vault, "discovery", "promoted.jsonl"))
    queued = queued_reclassifies(log)
    out = []
    for identity, rec, started in queued:
        proposal = rec.get("proposal") or {}
        from_entity = rec.get("from_entities") or []
        slug = (from_entity[0] if from_entity else None)
        if not slug and len(identity) >= 2:
            slug = identity[1]
        if only_slug and slug != only_slug:
            continue
        to_type = proposal.get("to_type")
        to_subtype = proposal.get("to_subtype")
        if not (slug and to_type and to_subtype):
            out.append({"slug": slug, "status": "skip",
                        "reason": "missing slug/to_type/to_subtype in proposal"})
            continue
        out.append(apply_reclassify(vault, slug, to_type, to_subtype,
                                    identity, proposal,
                                    started_rec=started, dry_run=dry_run))
    return out


def main():
    args = sys.argv[1:]
    vault = "."
    only_slug = None
    dry_run = False
    i = 0
    while i < len(args):
        a = args[i]
        if a in ("-h", "--help"):
            print(__doc__); return 0
        if a == "--slug" and i + 1 < len(args):
            only_slug = args[i + 1]; i += 2; continue
        if a == "--dry-run":
            dry_run = True
        elif not a.startswith("-"):
            vault = a
        i += 1

    results = apply_all(vault, only_slug=only_slug, dry_run=dry_run)
    applied = [r for r in results if r.get("status") == "applied"]
    skipped = [r for r in results if r.get("status") == "skip"]
    abandoned = [r for r in results if r.get("status") == "abandoned"]
    print(f"reclassify_apply{' (dry run)' if dry_run else ''}: "
          f"{len(applied)} applied, {len(skipped)} skipped, "
          f"{len(abandoned)} abandoned")
    for r in applied:
        print(f"  + {r['from']:32} -> {r['to']:32}  refs_updated={r['refs_updated']}")
    for r in skipped:
        print(f"  ~ {r.get('slug','?'):32} skipped: {r.get('reason','')}")
    for r in abandoned:
        print(f"  ! {r.get('slug','?'):32} abandoned: {r.get('reason','')}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

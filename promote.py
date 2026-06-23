#!/usr/bin/env python3
"""
promote.py — Stage 4. THE deterministic learning loop.

Reads `discovery/proposals.jsonl` (append-only, written by the compile pass;
never truncated — re-decisions are idempotent and sightings are counted by
DISTINCT source entity so recompiles can't inflate them),
normalizes each proposal through the alias map, and graduates good ones into
the registry per the thresholds in `registry/schema.yaml`'s `promotion:` block.
Everything else is parked in `discovery/promoted.jsonl` with `queue_for_review`
or `deferred_below_threshold` so a human can audit it later.

This is the system's anti-rot valve. The LLM proposes; this script alone
commits to the canonical vocabulary. Never let a non-deterministic component
write the vocabulary it later reads (spec §3, §5).

What this script writes:
  - `registry/schema.yaml` (new tags, new subtypes only; types are human-gated)
  - `registry/aliases.yaml` (new surface-form -> slug mappings)
  - `discovery/promoted.jsonl` (append-only audit log of every action)

What it must NEVER write:
  - any entity file (those belong to ingest/compile)
  - `registry/resolvers.yaml` or `_entity-template.md` (human only)
  - the prose body of anything

Usage:  python3 promote.py [VAULT_DIR]   (defaults to current dir)
        python3 promote.py . --dry-run   (show what would happen, write nothing)
"""
import sys, os, re, json, datetime

# Force UTF-8 stdout/stderr so entity titles and LLM-proposed concepts with
# Unicode don't crash on Windows consoles that default to cp1252.
for _s in (sys.stdout, sys.stderr):
    if hasattr(_s, "reconfigure"):
        _s.reconfigure(encoding="utf-8")

try:
    import yaml
except ImportError:
    sys.exit("pyyaml required:  pip install pyyaml --break-system-packages")

# vault-wide write lock (overlapping mutating runs serialize; readers don't lock)
try:
    from locking import vault_lock
except Exception:
    from contextlib import nullcontext
    def vault_lock(_v):
        return nullcontext()


# Confidence bands referenced from schema.yaml's `require_confidence: high|med|low`.
CONFIDENCE_BANDS = {"high": 0.80, "medium": 0.50, "low": 0.0}


# ============================================================================
# Loaders
# ============================================================================
def load_yaml(path):
    if not os.path.exists(path):
        return {}
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def load_proposals(vault):
    """Read every proposal record from discovery/proposals.jsonl in order.
    Each record was emitted by the compile pass; shape matches compiler.py:
        {ts, from_entity, from_sources, model, contract_version, proposal: {kind, ...}}
    """
    p = os.path.join(vault, "discovery", "proposals.jsonl")
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
            pass  # corrupt line: skip, don't poison the run
    return out


def load_promoted_log(vault):
    """Read past promotion actions so we don't double-process anything."""
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


def load_entity_slugs(vault):
    """Set of every existing entity, formatted as 'type/slug'. Used to verify
    proposed aliases/reclassifies actually point at something real."""
    out = set()
    edir = os.path.join(vault, "entities")
    for d, _, files in os.walk(edir):
        for fn in files:
            if fn.endswith(".md"):
                t = os.path.relpath(d, edir).replace("\\", "/")
                out.add(f"{t}/{os.path.splitext(fn)[0]}")
    return out


# ============================================================================
# Normalization
# ============================================================================
SPACE_RE = re.compile(r"\s+")


def norm_tag(name):
    """Tag normalization: lowercase, trim, internal spaces -> dashes.
    'Bank of America' / 'bank of america' / 'BANK OF AMERICA' -> 'bank-of-america'."""
    if name is None:
        return ""
    s = SPACE_RE.sub("-", str(name).strip().lower())
    return re.sub(r"-+", "-", s).strip("-")


def norm_alias_key(s):
    """Alias surface-form normalization: lowercase + collapse whitespace.
    Aliases are matched case-insensitively at lookup; storing them lowercased
    keeps the file readable and prevents bogus 'BoA' vs 'boa' duplicates."""
    return SPACE_RE.sub(" ", str(s or "").strip().lower())


def normalize_proposal(record, aliases_map):
    """Return a normalized copy of one proposal record. Surface-form fields
    pass through the alias map so 'BoA' and 'bofa' collapse to the same slug
    before we count sightings."""
    p = dict(record.get("proposal") or {})
    kind = p.get("kind")
    if kind == "tag":
        name = norm_tag(p.get("name"))
        # Spec §5.2: collapse surface-form variants through the alias map so
        # a proposed tag 'boa' and the canonical 'bank-of-america' count as
        # ONE concept. Alias keys are space-normalized surface forms; try the
        # dashed tag form and its space-separated equivalent.
        target = aliases_map.get(name) or aliases_map.get(name.replace("-", " "))
        p["name"] = norm_tag(target) if target else name
    elif kind == "alias":
        # `from` is a surface form (lowercased); `to` is a slug (passed through aliases)
        p["from"] = norm_alias_key(p.get("from"))
        target = str(p.get("to") or "").strip().lower()
        p["to"] = aliases_map.get(target, target)
    elif kind == "subtype":
        p["type"] = str(p.get("type") or "").strip().lower()
        p["name"] = norm_tag(p.get("name"))
    elif kind == "type":
        p["name"] = norm_tag(p.get("name"))
    elif kind == "reclassify":
        p["to_type"] = str(p.get("to_type") or "").strip().lower()
        p["to_subtype"] = str(p.get("to_subtype") or "").strip().lower()
    return {**record, "proposal": p, "_kind": kind}


# ============================================================================
# Aggregation — group normalized proposals by identity, sum sightings
# ============================================================================
def proposal_identity(p):
    """A stable key per kind that says 'this is the same concept'."""
    k = p.get("kind")
    if k == "tag":      return ("tag", p.get("name", ""))
    if k == "alias":    return ("alias", p.get("from", ""), p.get("to", ""))
    if k == "subtype":  return ("subtype", p.get("type", ""), p.get("name", ""))
    if k == "type":     return ("type", p.get("name", ""))
    if k == "reclassify":
        return ("reclassify", p.get("from_entity", ""),
                p.get("to_type", ""), p.get("to_subtype", ""))
    return ("unknown", json.dumps(p, sort_keys=True))


def aggregate(records):
    """records: list of normalized proposal envelopes.
    Returns: { identity_tuple -> aggregated dict with sightings, evidence, etc. }"""
    agg = {}
    for rec in records:
        p = rec["proposal"]
        # carry from_entity into the proposal for reclassify identity
        p2 = {**p, "from_entity": rec.get("from_entity")}
        ident = proposal_identity(p2)
        if ident not in agg:
            agg[ident] = {
                "identity": ident,
                "kind": p.get("kind"),
                "proposal": p,
                "sightings": 0,
                "max_confidence": 0.0,
                "evidence": [],
                "from_entities": [],
                "models": set(),
            }
        a = agg[ident]
        a["sightings"] += 1
        conf = p.get("confidence")
        if isinstance(conf, (int, float)):
            a["max_confidence"] = max(a["max_confidence"], float(conf))
        ev = p.get("evidence")
        if ev and ev not in a["evidence"]:
            a["evidence"].append(ev)
        fe = rec.get("from_entity")
        if fe and fe not in a["from_entities"]:
            a["from_entities"].append(fe)
        m = rec.get("model")
        if m: a["models"].add(m)
    # Make sets JSON-serializable for downstream logging
    for a in agg.values():
        a["models"] = sorted(a["models"])
    return agg


# ============================================================================
# Decision logic — apply the thresholds in schema.yaml's `promotion:` block
# ============================================================================
def decide(agg_item, thresholds, schema, registry_state):
    """For ONE aggregated identity, decide: auto_promote | queue_for_review |
    deferred_below_threshold | rejected_duplicate.

    `registry_state` is a snapshot of what's already in the registry so we
    don't re-promote (idempotent re-runs)."""
    k = agg_item["kind"]
    p = agg_item["proposal"]
    rule = thresholds.get(f"new_{k}") if k != "reclassify" else thresholds.get("reclassify")
    if not rule:
        return _decision("queue_for_review", "no promotion rule for this kind")

    # Reclassify rewrites entity files, not the registry. Stage 4 only owns
    # the registry; applying the reclassification to entities is a separate,
    # riskier operation (slug-rename, link-rewrite) we don't ship yet. Even
    # if the rule passes, we queue these for human review.
    if k == "reclassify":
        return _decision("queue_for_review",
                         "reclassify touches entity files; not auto-applied in Stage 4")

    # --- already-in-registry checks (idempotency) ---
    if k == "tag":
        if p["name"] in registry_state["tags"]:
            return _decision("rejected_duplicate", f"tag '{p['name']}' already in registry")
    elif k == "alias":
        if p["from"] in registry_state["aliases"]:
            return _decision("rejected_duplicate", f"alias '{p['from']}' already in registry")
    elif k == "subtype":
        sts = registry_state["subtypes_by_type"].get(p["type"], [])
        if p["name"] in sts:
            return _decision("rejected_duplicate", f"subtype '{p['type']}/{p['name']}' already in registry")
    elif k == "type":
        if p["name"] in registry_state["types"]:
            return _decision("rejected_duplicate", f"type '{p['name']}' already in registry")

    # --- auto: false means ALWAYS gated (new_type) ---
    if not rule.get("auto", False):
        return _decision("queue_for_review", "registry rule: auto=false (human-gated)")

    # --- min_sightings check (DISTINCT entities, not raw record count) ---
    # proposals.jsonl is append-only and an entity is re-proposed on every
    # recompile, so counting raw records would let ONE entity recompiled N times
    # cross the threshold and auto-promote vocabulary on a single entity's
    # evidence. Gate on distinct source entities instead. (Fall back to raw count
    # only for provenance-less proposals, which the compiler never produces.)
    min_sight = rule.get("min_sightings", 1)
    distinct = len(agg_item["from_entities"]) or agg_item["sightings"]
    if distinct < min_sight:
        return _decision("deferred_below_threshold",
                         f"distinct_entities={distinct} < {min_sight} "
                         f"(records={agg_item['sightings']})",
                         needs_more=True)

    # --- confidence check (when required) ---
    req_conf = rule.get("require_confidence")
    if req_conf:
        threshold = CONFIDENCE_BANDS.get(req_conf, 1.0)
        if agg_item["max_confidence"] < threshold:
            return _decision("queue_for_review",
                             f"max_confidence={agg_item['max_confidence']:.2f} "
                             f"< {req_conf} ({threshold:.2f})")

    # --- target-exists checks ---
    if k == "alias" and rule.get("require_target_exists"):
        slugs = registry_state["entity_slugs"]
        target = p["to"]
        # `to` may already be a 'type/slug' ref, or just a slug
        if "/" not in target:
            target_matches = [s for s in slugs if s.endswith("/" + target)]
        else:
            target_matches = [target] if target in slugs else []
        if not target_matches:
            return _decision("queue_for_review",
                             f"alias target '{target}' does not exist as an entity")

    if k == "subtype" and rule.get("require_parent_exists"):
        if p["type"] not in registry_state["types"]:
            return _decision("queue_for_review",
                             f"parent type '{p['type']}' is not in the registry")

    if k == "reclassify" and rule.get("require_target_in_registry"):
        t, st = p.get("to_type"), p.get("to_subtype")
        if t not in registry_state["types"]:
            return _decision("queue_for_review",
                             f"reclassify target type '{t}' not in registry")
        sts = registry_state["subtypes_by_type"].get(t, [])
        if st and st not in sts:
            return _decision("queue_for_review",
                             f"reclassify subtype '{t}/{st}' not in registry")

    # --- All checks passed: auto-promote ---
    return _decision("auto_promote", f"auto: kind={k}, sightings={agg_item['sightings']}, "
                                     f"max_conf={agg_item['max_confidence']:.2f}")


def _decision(action, reason, **extra):
    return {"action": action, "reason": reason, **extra}


# ============================================================================
# Apply promotions — the registry writers
# ============================================================================
def _atomic_write(path, text):
    """Write `text` to `path` via tmp+rename so an interrupted run cannot
    leave a half-written registry file. Mirrors ingest.py / build_index.py."""
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(text)
        f.flush(); os.fsync(f.fileno())
    os.replace(tmp, path)


def _yaml_quote_key(s):
    """Quote a YAML scalar key safely. Conservative: escapes embedded
    backslashes, quotes, newlines, tabs, and carriage returns; always wraps
    the result in double quotes. Used for alias surface forms which can
    contain anything a vendor name (or a buggy upstream proposal) throws
    at us — including newlines that would otherwise break the YAML line."""
    s = str(s)
    s = s.replace("\\", "\\\\")
    s = s.replace('"', '\\"')
    s = s.replace("\n", "\\n")
    s = s.replace("\r", "\\r")
    s = s.replace("\t", "\\t")
    return '"' + s + '"'


def apply_tag(vault, name, agg, today, dry_run=False):
    """Insert a new tag entry into schema.yaml's `tags:` block, preserving
    surrounding comments and order. Atomic via tmp+rename."""
    path = os.path.join(vault, "registry", "schema.yaml")
    text = open(path, encoding="utf-8").read()
    line = f"  {name}: {{ promoted: {today}, count: {agg['sightings']} }}"
    new_text = _insert_in_block(text, "tags", line)
    if new_text == text:
        return False
    if not dry_run:
        _atomic_write(path, new_text)
    return True


def apply_alias(vault, surface, target, dry_run=False):
    """Append a new alias entry to aliases.yaml's `aliases:` block. Surface
    forms come from LLM proposals — quote/escape them so a vendor name with
    a quote or backslash can't corrupt the registry."""
    path = os.path.join(vault, "registry", "aliases.yaml")
    text = open(path, encoding="utf-8").read()
    # Strip "type/" prefix if present — aliases.yaml stores bare slugs.
    bare = target.split("/", 1)[-1]
    line = f"  {_yaml_quote_key(surface)}: {bare}"
    new_text = _insert_in_block(text, "aliases", line)
    if new_text == text:
        return False
    if not dry_run:
        _atomic_write(path, new_text)
    return True


def apply_subtype(vault, parent_type, name, dry_run=False):
    """Add `name` to the inline subtypes list of `parent_type` in schema.yaml.
    Handles both `subtypes: [a, b, c]` and the wrapped multi-line form.
    Atomic via tmp+rename."""
    path = os.path.join(vault, "registry", "schema.yaml")
    text = open(path, encoding="utf-8").read()
    # The scan between the type key and its `subtypes:` line may only cross
    # lines belonging to that type's own block (deeper-indented or blank).
    # An unconstrained lazy `.*` would walk past the next 2-space type key
    # and append the subtype to the WRONG type's list when the parent has
    # no `subtypes:` line at all.
    type_re = re.compile(
        rf"(^  {re.escape(parent_type)}:\n(?:(?:    [^\n]*|[ \t]*)\n)*?    subtypes:[ \t]*)\[([^\]]*)\]",
        re.M,
    )
    m = type_re.search(text)
    if not m:
        raise ValueError(
            f"apply_subtype: cannot find `{parent_type}:` block with a "
            f"`subtypes: [...]` line in schema.yaml. Likely schema drift "
            f"(type removed or restructured). Refusing to silently no-op."
        )
    items = [s.strip() for s in m.group(2).split(",") if s.strip()]
    if name in items:
        return False
    items.append(name)
    new_list = ", ".join(items)
    new_line = m.group(1) + "[" + new_list + "]"
    new_text = text[:m.start()] + new_line + text[m.end():]
    if not dry_run:
        _atomic_write(path, new_text)
    return True


def apply_type(vault, name, today, dry_run=False):
    """Insert a NEW top-level entity type into schema.yaml's `types:` block.
    Only ever called from the human approve path (review.py) — `new_type`
    promotion is `auto: false` in every sane registry, so promote.py itself
    never invokes this. The block written is a minimal valid entry the human
    is expected to refine (description, real subtypes) by hand afterwards;
    validate.py only requires that entity subtypes appear in the list.
    Atomic via tmp+rename. Returns False if the type already exists."""
    if not re.match(r"^[a-z0-9]+(?:-[a-z0-9]+)*$", str(name or "")):
        raise ValueError(f"apply_type: {name!r} is not a valid type slug")
    path = os.path.join(vault, "registry", "schema.yaml")
    text = open(path, encoding="utf-8").read()
    if re.search(rf"^  {re.escape(name)}:", text, re.M):
        return False
    entry = (f"\n  {name}:\n"
             f"    description: (promoted via review {today} — refine by hand)\n"
             f"    subtypes: [general]\n"
             f"    page_template: generic\n"
             f"    human_gated: false")
    new_text = _insert_in_block(text, "types", entry)
    if new_text == text:
        return False
    if not dry_run:
        _atomic_write(path, new_text)
        os.makedirs(os.path.join(vault, "entities", name), exist_ok=True)
    return True


# ----------------------------------------------------------------------------
# YAML text helpers — narrow, comment-preserving edits
# ----------------------------------------------------------------------------
def _insert_in_block(text, block_key, new_line):
    """Insert `new_line` after the LAST indented entry of the `block_key:`
    mapping. The block ends at the first dedented top-level key (or EOF).
    Internal blank lines (used for visual grouping) are tolerated — we
    anchor to the last indented line, not the first blank, so we don't
    accidentally insert under a different key.

    Raises ValueError if `block_key:` is not present at top level. Silently
    appending to EOF would land the entry under whatever the last top-level
    mapping happens to be, corrupting the registry without warning.

    Idempotent: if the exact line is already present, the text is returned
    unchanged."""
    if new_line in text:
        return text
    if not text.endswith("\n"):
        text += "\n"
    lines = text.splitlines(keepends=True)
    in_block = False
    last_indented_idx = None
    insert_idx = None
    for i, line in enumerate(lines):
        stripped = line.rstrip("\n")
        if not in_block:
            if stripped == f"{block_key}:" or stripped.startswith(f"{block_key}:"):
                in_block = True
                last_indented_idx = i  # so we insert immediately after the key
                                       # if the block is empty
            continue
        # Dedented non-empty = next top-level key; block ended.
        if stripped and not stripped[0].isspace():
            insert_idx = i
            break
        # Indented (or blank inside block): track the last truly indented line
        # we've seen — that's where we'll insert.
        if stripped.strip() != "":
            last_indented_idx = i
    if not in_block:
        raise ValueError(
            f"_insert_in_block: no top-level `{block_key}:` block found "
            f"in registry file; refusing to silently corrupt by appending "
            f"to EOF. Add the block manually first."
        )
    if insert_idx is None:
        # Block ran to EOF — insert after the last indented line if we saw one.
        insert_idx = (last_indented_idx + 1) if last_indented_idx is not None \
            else len(lines)
    else:
        # Insert right AFTER the last indented line, not at the dedent.
        insert_idx = (last_indented_idx + 1) if last_indented_idx is not None \
            else insert_idx
    lines.insert(insert_idx, new_line + "\n")
    return "".join(lines)


# ============================================================================
# Promoted log — append-only audit of every decision
# ============================================================================
def already_processed(promoted_log, identity):
    """Return the most recent action recorded for this identity, or None."""
    last = None
    for r in promoted_log:
        if tuple(r.get("identity") or ()) == tuple(identity):
            last = r
    return last


def log_action(vault, identity, kind, agg, decision, today_iso, dry_run=False):
    path = os.path.join(vault, "discovery", "promoted.jsonl")
    record = {
        "ts": today_iso,
        "identity": list(identity),
        "kind": kind,
        "action": decision["action"],
        "reason": decision["reason"],
        "sightings": agg["sightings"],
        "max_confidence": round(agg["max_confidence"], 2),
        "from_entities": agg["from_entities"][:8],
        "models": agg["models"],
        "evidence_count": len(agg["evidence"]),
        "proposal": agg["proposal"],
    }
    if not dry_run:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(record) + "\n")
    return record


# ============================================================================
# Driver
# ============================================================================
def registry_snapshot(vault):
    """Snapshot of registry state needed by decide(): tags, aliases, types,
    subtypes_by_type, plus the set of existing entity slugs for alias-target
    existence checks."""
    schema = load_yaml(os.path.join(vault, "registry", "schema.yaml"))
    aliases = load_yaml(os.path.join(vault, "registry", "aliases.yaml"))
    types = schema.get("types") or {}
    return {
        "tags": set((schema.get("tags") or {}).keys()),
        "aliases": set((aliases.get("aliases") or {}).keys()),
        "types": set(types.keys()),
        "subtypes_by_type": {t: list(types[t].get("subtypes") or []) for t in types},
        "entity_slugs": load_entity_slugs(vault),
        "thresholds": schema.get("promotion") or {},
    }


def promote(vault, dry_run=False):
    """One full pass: read proposals -> normalize -> aggregate -> decide ->
    apply -> log. Returns a summary dict.

    The whole pass holds the vault-wide exclusive lock (fcntl.flock on
    `registry/_vault.lock`, via locking.vault_lock) so two concurrent runs
    serialize instead of racing — each would otherwise read the same
    baseline and silently clobber the other's writes."""
    with vault_lock(vault):
        return _promote_unlocked(vault, dry_run)


def _promote_unlocked(vault, dry_run):
    schema_path = os.path.join(vault, "registry", "schema.yaml")
    schema = load_yaml(schema_path)
    aliases = load_yaml(os.path.join(vault, "registry", "aliases.yaml"))
    aliases_map = {norm_alias_key(k): v for k, v in (aliases.get("aliases") or {}).items()}
    state = registry_snapshot(vault)
    thresholds = state["thresholds"]

    raw_proposals = load_proposals(vault)
    promoted_log = load_promoted_log(vault)
    normed = [normalize_proposal(r, aliases_map) for r in raw_proposals]
    agg = aggregate(normed)

    today = datetime.date.today().isoformat()
    today_iso = datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")
    summary = {
        "proposals_in": len(raw_proposals),
        "groups": len(agg),
        "auto_promoted": [], "queued": [], "deferred": [], "duplicate": [],
        "dry_run": dry_run,
    }

    for identity in sorted(agg.keys(), key=lambda t: tuple(str(x) for x in t)):
        a = agg[identity]
        prev = already_processed(promoted_log, identity)
        # Re-decide every run from current state — that's the source of truth.
        decision = decide(a, thresholds, schema, state)

        # Don't re-log a queue/defer action that's identical to last time.
        if prev and prev.get("action") == decision["action"] and \
           prev.get("sightings") == a["sightings"] and \
           decision["action"] in ("queue_for_review", "deferred_below_threshold", "rejected_duplicate"):
            continue
        # A human rejection (review.py) stands until NEW evidence arrives:
        # the proposal is still in proposals.jsonl, so decide() would re-queue
        # it on every run. Skip unless the sighting count has grown.
        if prev and prev.get("action") == "review_rejected" and \
           decision["action"] == "queue_for_review" and \
           prev.get("sightings") == a["sightings"]:
            continue
        # A human approval already wrote the registry; the follow-up
        # rejected_duplicate decision is implicit — don't clutter the ledger.
        if prev and prev.get("action") == "review_approved" and \
           decision["action"] == "rejected_duplicate":
            continue

        applied = False
        if decision["action"] == "auto_promote":
            kind = a["kind"]
            p = a["proposal"]
            try:
                if kind == "tag":
                    applied = apply_tag(vault, p["name"], a, today, dry_run=dry_run)
                    if applied:
                        state["tags"].add(p["name"])
                elif kind == "alias":
                    applied = apply_alias(vault, p["from"], p["to"], dry_run=dry_run)
                    if applied:
                        state["aliases"].add(p["from"])
                elif kind == "subtype":
                    applied = apply_subtype(vault, p["type"], p["name"], dry_run=dry_run)
                    if applied:
                        state["subtypes_by_type"].setdefault(p["type"], []).append(p["name"])
            except ValueError as e:
                # Registry-shape errors (missing block, schema drift). Queue
                # this one for human review rather than crashing the whole
                # promote pass — other proposals may still be applicable.
                decision = _decision("queue_for_review",
                                     f"registry edit failed: {e}")

            # If apply_*() returned False, the registry already had the
            # entry. Treat as duplicate so the log doesn't fill with no-op
            # auto_promote records on every run.
            if not applied and decision["action"] == "auto_promote":
                decision = _decision("rejected_duplicate",
                                     f"{kind} {p.get('name', p.get('from','?'))!r} "
                                     f"already present in registry")

        rec = log_action(vault, identity, a["kind"], a, decision, today_iso, dry_run=dry_run)
        bucket = {"auto_promote": "auto_promoted",
                  "queue_for_review": "queued",
                  "deferred_below_threshold": "deferred",
                  "rejected_duplicate": "duplicate"}.get(decision["action"], "queued")
        summary[bucket].append(rec)

    return summary


# ============================================================================
# CLI
# ============================================================================
def main():
    args = sys.argv[1:]
    vault = "."
    dry_run = False
    for a in args:
        if a in ("-h", "--help"):
            print(__doc__); return 0
        if a == "--dry-run":
            dry_run = True
        elif not a.startswith("-"):
            vault = a

    s = promote(vault, dry_run=dry_run)
    print(f"promotion pass{' (DRY RUN)' if dry_run else ''}: "
          f"{s['proposals_in']} proposals -> {s['groups']} distinct concepts")
    print(f"  auto-promoted: {len(s['auto_promoted'])}")
    print(f"  queued for review: {len(s['queued'])}")
    print(f"  deferred (under threshold): {len(s['deferred'])}")
    print(f"  duplicate (already in registry): {len(s['duplicate'])}")

    for rec in s["auto_promoted"]:
        ident = "/".join(str(x) for x in rec["identity"])
        print(f"\n  AUTO   {ident}")
        print(f"         {rec['reason']}")
    for rec in s["queued"]:
        ident = "/".join(str(x) for x in rec["identity"])
        print(f"\n  QUEUE  {ident}")
        print(f"         {rec['reason']}")
    for rec in s["deferred"]:
        ident = "/".join(str(x) for x in rec["identity"])
        print(f"\n  WAIT   {ident}  (sightings={rec['sightings']})")
    return 0


if __name__ == "__main__":
    sys.exit(main())

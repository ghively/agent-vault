#!/usr/bin/env python3
"""
compact.py — bound the append-only discovery logs WITHOUT changing any promotion
outcome. Maintenance op (run e.g. quarterly, or wire into a cadence).

Why this is safe:
  - discovery/proposals.jsonl: the compile pass appends a proposal on EVERY
    recompile of an entity, so the log grows unboundedly with duplicate records.
    promote.aggregate counts DISTINCT source entities, so keeping exactly one
    record per (from_entity, proposal-identity) preserves every sighting count
    and therefore every decision.
  - discovery/promoted.jsonl: promote.already_processed() only ever consults the
    MOST RECENT record for an identity, so keeping the last record per identity
    preserves idempotency exactly.

Each compaction writes a `<log>.bak` snapshot first, then rewrites atomically
(tmp + fsync + rename). Corrupt JSON lines are dropped (they're already ignored
by the readers). Dry-run by default.

Usage:
    python -m agent_vault.compact [VAULT_DIR]            # report only (no writes)
    python -m agent_vault.compact [VAULT_DIR] --apply    # compact in place (+ .bak backups)
"""
import sys
import os
import json
import shutil

# Force UTF-8 stdout/stderr so em-dashes and other Unicode don't crash on
# Windows consoles that default to cp1252.
for _s in (sys.stdout, sys.stderr):
    if hasattr(_s, "reconfigure"):
        _s.reconfigure(encoding="utf-8")

try:
    from .locking import vault_lock
except Exception:
    from contextlib import nullcontext
    def vault_lock(_v):
        return nullcontext()


def _load(path):
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
            pass  # drop corrupt lines (readers already skip them)
    return out


def _atomic_write_lines(path, records):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        for r in records:
            f.write(json.dumps(r) + "\n")
        f.flush(); os.fsync(f.fileno())
    os.replace(tmp, path)


def _proposal_identity(rec):
    """Stable identity for a proposal record, matching promote's aggregate."""
    try:
        from .promote import proposal_identity
    except Exception:
        # standalone fallback: identify by kind + the salient name fields
        p = rec.get("proposal") or {}
        return (p.get("kind"), p.get("name"), p.get("from"), p.get("to"),
                p.get("type"), p.get("to_type"), p.get("to_subtype"))
    p = dict(rec.get("proposal") or {})
    p2 = {**p, "from_entity": rec.get("from_entity")}
    return proposal_identity(p2)


def _confidence(rec):
    c = (rec.get("proposal") or {}).get("confidence")
    return float(c) if isinstance(c, (int, float)) else 0.0


def compact_proposals(records):
    """Keep ONE record per (from_entity, proposal-identity): the highest-
    confidence one (ties -> most recent). promote.aggregate gates some kinds
    on MAX confidence across records, so keeping merely the last record could
    lower max_confidence and flip an auto_promote to queue_for_review —
    violating this module's no-outcome-change guarantee."""
    seen = {}
    for r in records:
        key = (r.get("from_entity"), _proposal_identity(r))
        cur = seen.get(key)
        if cur is None or _confidence(r) >= _confidence(cur):
            seen[key] = r
    return sorted(seen.values(), key=lambda r: r.get("ts", ""))


def compact_promoted(records):
    """Keep the last record per identity (already_processed uses most-recent)."""
    seen = {}
    for r in records:
        seen[tuple(r.get("identity") or ())] = r
    return sorted(seen.values(), key=lambda r: r.get("ts", ""))


TARGETS = [
    ("discovery/proposals.jsonl", compact_proposals),
    ("discovery/promoted.jsonl", compact_promoted),
]


def compact_vault(vault, apply=False):
    """Return {relpath: {before, after}}; rewrite in place when apply=True."""
    sys.path.insert(0, vault)  # so `from promote import ...` resolves
    results = {}
    with vault_lock(vault):
        for rel, fn in TARGETS:
            path = os.path.join(vault, rel)
            before = _load(path)
            after = fn(before)
            results[rel] = {"before": len(before), "after": len(after)}
            if apply and len(after) < len(before):
                shutil.copy2(path, path + ".bak")
                _atomic_write_lines(path, after)
    return results


def main():
    args = sys.argv[1:]
    apply = "--apply" in args
    rest = [a for a in args if not a.startswith("-")]
    vault = rest[0] if rest else "."
    results = compact_vault(vault, apply=apply)
    total_saved = 0
    for rel, r in results.items():
        saved = r["before"] - r["after"]
        total_saved += saved
        print(f"  {rel:32} {r['before']:6} -> {r['after']:6}  ({saved} removed)")
    if apply:
        print(f"compacted: {total_saved} record(s) removed (.bak backups written)")
    else:
        print(f"dry-run: {total_saved} record(s) would be removed — pass --apply")
    return 0


if __name__ == "__main__":
    sys.exit(main())

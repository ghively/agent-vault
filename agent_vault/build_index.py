#!/usr/bin/env python3
"""
build_index.py — walk entities/, emit _index.json (and a human _index.md).

Stage 1. Pure stdlib + pyyaml. No LLM, no network.
This is what makes Agent Vault a pure lookup: every queryable field is pre-extracted here,
so retrieval is "filter the index", never "reason over text".

Usage:  python -m agent_vault.build_index [VAULT_DIR]   # defaults to current dir
"""
import sys
import os
import re
import glob
import json
import datetime

# Force UTF-8 stdout/stderr so entity titles with Unicode don't crash on
# Windows consoles that default to cp1252.
for _s in (sys.stdout, sys.stderr):
    if hasattr(_s, "reconfigure"):
        _s.reconfigure(encoding="utf-8")

try:
    import yaml
except ImportError:
    sys.exit("pyyaml required:  pip install pyyaml --break-system-packages")

FM_RE = re.compile(r"^---\n(.*?)\n---\n(.*)$", re.S)

# fields lifted verbatim into the index if present
PASSTHROUGH = ["slug", "type", "subtype", "title", "status", "confidence",
               "tags", "aliases", "location", "value", "value_as_of",
               "serial", "model", "vin", "last4", "created", "related"]
# date fields collected under "dates" for temporal queries
DATE_FIELDS = ["acquired", "expires", "renews", "serviced", "due"]


def parse_entity(path, vault="."):
    raw = open(path, encoding="utf-8").read()
    m = FM_RE.match(raw)
    if not m:
        return None
    # One malformed entity must not abort the whole index build — warn and
    # skip it, same posture as the other frontmatter readers.
    try:
        fm = yaml.safe_load(m.group(1)) or {}
    except yaml.YAMLError as e:
        print(f"warn: skipping {path}: frontmatter is not valid YAML ({e})",
              file=sys.stderr)
        return None
    if not isinstance(fm, dict):
        print(f"warn: skipping {path}: frontmatter is not a mapping",
              file=sys.stderr)
        return None
    # Pin path relative to the vault, not to cwd — keeps the index stable
    # no matter where build_index.py is invoked from (compiler.py calls it
    # in-process, so cwd may not be the vault).
    rec = {"path": os.path.relpath(path, vault).replace("\\", "/")}
    for k in PASSTHROUGH:
        if k in fm and fm[k] not in (None, "", []):
            v = fm[k]
            if isinstance(v, (datetime.date, datetime.datetime)):
                v = str(v)
            rec[k] = v
    dates = {k: str(fm[k]) for k in DATE_FIELDS if fm.get(k)}
    if dates:
        rec["dates"] = dates
    rec["has_credential"] = bool(fm.get("credential_ref"))
    # normalize aliases to lowercase for matching; keep title as a searchable
    # alias too. str() coercion: a non-string slug/title (e.g. `title: 1984`)
    # must not crash the build.
    al = [str(a).lower() for a in (fm.get("aliases") or [])]
    rec["_match"] = list({str(rec.get("slug", "")).lower(),
                          str(rec.get("title", "")).lower(), *al} - {""})
    return rec


def reindex(vault, quiet=False):
    """Rebuild _index.json + _index.md + the FTS sidecar from entities/.

    The single, argv-free entry point every mutating pass should call after it
    changes an entity file, so readers (find/show/`GET /api/*`) never serve a
    stale index. Returns the entity count. `quiet` suppresses stdout (used by
    in-process callers). Deterministic; safe to call under the vault lock.
    """
    files = sorted(glob.glob(os.path.join(vault, "entities", "*", "*.md")))
    entities = [r for r in (parse_entity(f, vault) for f in files) if r]

    index = {
        "generated": datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds"),
        "count": len(entities),
        "entities": entities,
    }
    out_json = os.path.join(vault, "_index.json")
    tmp = out_json + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(index, f, indent=2)
        f.flush(); os.fsync(f.fileno())
    os.replace(tmp, out_json)

    # human-readable companion index, grouped by type
    lines = [f"# Index ({len(entities)} entities)\n",
             f"_generated {index['generated']}_\n"]
    by_type = {}
    for e in entities:
        by_type.setdefault(str(e.get("type", "?")), []).append(e)
    for t in sorted(by_type):
        lines.append(f"\n## {t}\n")
        # str() sort keys: a non-string title (e.g. `title: 1984`) must not
        # crash the human-index render with a str/int comparison.
        for e in sorted(by_type[t], key=lambda x: str(x.get("title", ""))):
            tags = f"  `{', '.join(e['tags'])}`" if e.get("tags") else ""
            lines.append(f"- **{e.get('title','?')}** ({e.get('subtype','')}) — `{e['slug']}`{tags}")
    out_md = os.path.join(vault, "_index.md")
    tmp = out_md + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
        f.flush(); os.fsync(f.fileno())
    os.replace(tmp, out_md)

    if not quiet:
        print(f"indexed {len(entities)} entities -> {out_json}")

    # Refresh the full-text search sidecar (_index.db) from the same entities.
    # Best-effort: a search-index failure must never fail the structural index
    # build that retrieval's correctness depends on. FTS is an accelerator; the
    # substring fallback in search.py covers its absence.
    try:
        from agent_vault import search
        n = search.build_search_index(vault)
        if not quiet and n:
            print(f"search index: {n} entities -> {os.path.join(vault, search.DB_NAME)}")
        elif not quiet and not search.fts5_available():
            print("search index: FTS5 unavailable; retrieval uses substring fallback",
                  file=sys.stderr)
    except Exception as e:  # noqa: BLE001 - never let search indexing abort build
        print(f"warn: search index build skipped ({type(e).__name__}: {e})",
              file=sys.stderr)
    return len(entities)


def main():
    args = sys.argv[1:]
    if args and args[0] in ("-h", "--help"):
        print(__doc__); return 0
    vault = args[0] if args else "."
    reindex(vault)


if __name__ == "__main__":
    sys.exit(main())

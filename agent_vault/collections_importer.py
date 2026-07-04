#!/usr/bin/env python3
"""
collections_importer.py — Stage 6. Bookkeeping ingestion for catalog media.

You don't have local files for every game/book/movie/show you own — those
live on Steam, Plex, Goodreads, iTunes, Kindle, etc. This script takes a
library export (CSV or JSON) and creates one `entities/collection/<slug>.md`
per row. The taxonomy doesn't change; only the source.

Why a separate script? Two reasons:
  1. The unit of work is "one row" not "one file" — different from ingest.py.
  2. These are bookkeeping records, not narrative ones. No LLM compile is
     needed; entities are written with status:compiled and matching hashes
     so the compile pass leaves them alone.

What it understands today (no external network calls; just parses exports):
  - Steam library CSV/JSON  (presence of 'appid' / 'app_id' column)
  - Goodreads CSV           (presence of 'ISBN' or 'ISBN13' column)
  - IMDB list CSV           (presence of 'Title Type' column)
  - Generic CSV/JSON        (must have at minimum 'title' and 'subtype' cols)

Idempotent: re-importing the same export does nothing (slug-level merge).

Usage:
  python -m agent_vault.collections_importer [VAULT] --source raw/collections/steam.csv
  python -m agent_vault.collections_importer [VAULT] --source raw/collections/  # walk dir
"""
import sys
import os
import re
import csv
import json
import hashlib
import datetime

# Force UTF-8 stdout/stderr so media titles with Unicode don't crash on
# Windows consoles that default to cp1252.
for _s in (sys.stdout, sys.stderr):
    if hasattr(_s, "reconfigure"):
        _s.reconfigure(encoding="utf-8")

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


SLUG_RE = re.compile(r"[^a-z0-9]+")
COLLECTION_DIR = "entities/collection"
IMPORTS_LOG = "raw/collections/_imports.jsonl"


# ============================================================================
# Format detection
# ============================================================================
def detect_format(headers):
    """Return one of: steam, goodreads, imdb, letterboxd, discogs, kindle,
    audible, generic, unknown."""
    h = {x.lower().strip() for x in headers}
    if "appid" in h or "app_id" in h or "app id" in h:
        return "steam"
    if "letterboxd uri" in h:
        return "letterboxd"
    if "catalog#" in h or "release_id" in h or "collectionfolder" in h:
        return "discogs"
    if "narrator" in h:                       # Audible export (audiobooks)
        return "audible"
    if "asin" in h and ("title" in h or "name" in h):
        return "kindle"
    if "isbn" in h or "isbn13" in h:
        return "goodreads"
    if "title type" in h:
        return "imdb"
    if "title" in h and "subtype" in h:
        return "generic"
    return "unknown"


# ============================================================================
# Row -> entity dict normalizers (one per format)
# ============================================================================
def _slugify(s):
    s = SLUG_RE.sub("-", str(s).lower()).strip("-")
    return re.sub(r"-+", "-", s) or "untitled"


def _ci_get(row, *keys):
    """Case-insensitive lookup, first hit wins."""
    low = {k.lower().strip(): v for k, v in row.items()}
    for k in keys:
        v = low.get(k.lower())
        if v is not None and str(v).strip() != "":
            return str(v).strip()
    return None


def from_steam(row):
    title = _ci_get(row, "name", "title")
    if not title:
        return None
    appid = _ci_get(row, "appid", "app_id", "app id")
    slug_root = _slugify(title)
    return {
        "_slug_base": slug_root,
        "subtype": "game",
        "title": title,
        "identifier": f"steam:{appid}" if appid else None,
        "platform": "steam",
        "hours_played": _to_num(_ci_get(row, "hours_on_record", "playtime_forever",
                                        "hours on record", "hours_played")),
        "last_played": _ci_get(row, "last_played", "rtime_last_played"),
    }


def from_goodreads(row):
    title = _ci_get(row, "title")
    if not title:
        return None
    author = _ci_get(row, "author", "author l-f")
    isbn = _ci_get(row, "isbn13", "isbn")
    year = _ci_get(row, "year published", "original publication year")
    slug_root = _slugify(title)
    return {
        "_slug_base": slug_root,
        "subtype": "book",
        "title": title,
        "author": author,
        "year": _to_num(year),
        "identifier": f"isbn:{re.sub(r'[^0-9X]', '', isbn)}" if isbn else None,
        "platform": "goodreads",
        "rating": _to_num(_ci_get(row, "my rating", "rating")),
        "date_read": _ci_get(row, "date read"),
    }


def from_imdb(row):
    title = _ci_get(row, "title")
    if not title:
        return None
    title_type = (_ci_get(row, "title type") or "").lower()
    # IMDB title types: movie, tvSeries, tvMiniSeries, tvSpecial, tvMovie, short, video
    if "movie" in title_type:
        subtype = "film"
    elif "tv" in title_type or "series" in title_type:
        subtype = "tv"
    else:
        subtype = "film"  # safe default
    imdb_id = _ci_get(row, "const", "imdb id")
    year = _ci_get(row, "year")
    return {
        "_slug_base": _slugify(title),
        "subtype": subtype,
        "title": title,
        "year": _to_num(year),
        "identifier": f"imdb:{imdb_id}" if imdb_id else None,
        "platform": "imdb",
        "rating": _to_num(_ci_get(row, "your rating", "imdb rating")),
        "runtime_minutes": _to_num(_ci_get(row, "runtime (mins)", "runtime")),
        "genre": _ci_get(row, "genres"),
    }


def from_generic(row):
    title = _ci_get(row, "title")
    subtype = _ci_get(row, "subtype")
    if not title or not subtype:
        return None
    return {
        "_slug_base": _slugify(title),
        "subtype": subtype,
        "title": title,
        "identifier": _ci_get(row, "identifier", "id"),
        "platform": _ci_get(row, "platform"),
        "year": _to_num(_ci_get(row, "year")),
        "author": _ci_get(row, "author", "creator"),
        "genre": _ci_get(row, "genre"),
    }


def from_letterboxd(row):
    title = _ci_get(row, "name", "title")
    if not title:
        return None
    uri = _ci_get(row, "letterboxd uri")
    slug_part = uri.rstrip("/").rsplit("/", 1)[-1] if uri else None
    return {
        "_slug_base": _slugify(title),
        "subtype": "film",
        "title": title,
        "year": _to_num(_ci_get(row, "year")),
        "identifier": f"letterboxd:{slug_part}" if slug_part else None,
        "platform": "letterboxd",
        "rating": _to_num(_ci_get(row, "rating")),
    }


def from_discogs(row):
    title = _ci_get(row, "title")
    if not title:
        return None
    rid = _ci_get(row, "release_id")
    cat = _ci_get(row, "catalog#", "catalog")
    ident = f"discogs:{rid}" if rid else (f"discogs:{_slugify(cat)}" if cat else None)
    return {
        "_slug_base": _slugify(title),
        "subtype": "music",
        "title": title,
        "author": _ci_get(row, "artist"),
        "year": _to_num(_ci_get(row, "released", "year")),
        "identifier": ident,
        "platform": "discogs",
        "genre": _ci_get(row, "format", "genre"),
    }


def from_kindle(row):
    title = _ci_get(row, "title", "name")
    if not title:
        return None
    asin = _ci_get(row, "asin")
    return {
        "_slug_base": _slugify(title),
        "subtype": "book",
        "title": title,
        "author": _ci_get(row, "author", "authors"),
        "identifier": f"asin:{asin}" if asin else None,
        "platform": "kindle",
    }


def from_audible(row):
    title = _ci_get(row, "title", "name")
    if not title:
        return None
    asin = _ci_get(row, "asin")
    return {
        "_slug_base": _slugify(title),
        "subtype": "book",                    # audiobook -> book (closest subtype)
        "title": title,
        "author": _ci_get(row, "author", "authors"),
        "identifier": f"asin:{asin}" if asin else None,
        "platform": "audible",
    }


FORMAT_HANDLERS = {
    "steam":      from_steam,
    "goodreads":  from_goodreads,
    "imdb":       from_imdb,
    "letterboxd": from_letterboxd,
    "discogs":    from_discogs,
    "kindle":     from_kindle,
    "audible":    from_audible,
    "generic":    from_generic,
}


def _to_num(s):
    if s is None or s == "":
        return None
    try:
        f = float(s)
        return int(f) if f.is_integer() else f
    except (ValueError, TypeError):
        return None


# ============================================================================
# Readers — CSV and JSON
# ============================================================================
def read_rows(path):
    """Yield dict rows from a CSV or JSON file. Returns (headers, [row, ...])."""
    ext = os.path.splitext(path)[1].lower()
    if ext == ".csv":
        with open(path, encoding="utf-8-sig", newline="") as f:
            reader = csv.DictReader(f)
            headers = reader.fieldnames or []
            rows = list(reader)
            return headers, rows
    if ext == ".json":
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        # Accept either a list of records, or {"items": [...]} / {"data": [...]}
        if isinstance(data, dict):
            data = data.get("items") or data.get("data") or data.get("rows") or []
        if not isinstance(data, list) or not data:
            return [], []
        # Headers = union of keys across rows (deterministic order). Non-dict
        # rows contribute no headers; the per-row handler records them as bad.
        seen = []
        for r in data:
            if not isinstance(r, dict):
                continue
            for k in r:
                if k not in seen:
                    seen.append(k)
        return seen, data
    raise ValueError(f"unsupported extension {ext!r} (use .csv or .json)")


# ============================================================================
# Entity writer
# ============================================================================
def collection_subtypes(vault):
    with open(os.path.join(vault, "registry", "schema.yaml")) as f:
        schema = yaml.safe_load(f)
    return set((schema.get("types", {}).get("collection", {}).get("subtypes") or []))


def write_entity(vault, entity, source_path, today):
    """Write entities/collection/<slug>.md. Returns (path, created_bool).

    On slug collision with a different identifier, suffixes the slug
    (with year/platform when available, then with -2, -3 …).

    NOTE: When an existing entity has the SAME identifier, this function
    short-circuits — it does NOT refresh the row's other fields. So a
    re-export with updated hours_played / rating won't overwrite. That's
    deliberate (these stubs may have been hand-edited); use --refresh
    in a future iteration if you want the converging-truth behavior."""
    slug = entity["_slug_base"]
    target_dir = os.path.join(vault, COLLECTION_DIR)
    os.makedirs(target_dir, exist_ok=True)
    path = os.path.join(target_dir, f"{slug}.md")
    if os.path.exists(path):
        if _same_logical_entity(path, entity):
            return path, False  # same logical entity, keep what's there
        # A DIFFERENT logical entity occupies the base slug: disambiguate.
        # Try year, then platform, then a numeric counter.
        chosen = None
        for suffix in (entity.get("year"), entity.get("platform")):
            if not suffix:
                continue
            cand = f"{slug}-{_slugify(str(suffix))}"
            cand_path = os.path.join(target_dir, f"{cand}.md")
            if not os.path.exists(cand_path):
                chosen = (cand, cand_path)
                break
            if _same_logical_entity(cand_path, entity):
                return cand_path, False
        if chosen is None:
            # Numeric counter fallback. Suffix the BASE slug (x-2, x-3, ...)
            # — reassigning `slug` inside the loop would compound (x-2-3-4)
            # and the existence check must come first so we never report a
            # never-written path as "skipped".
            n = 2
            while True:
                cand = f"{slug}-{n}"
                cand_path = os.path.join(target_dir, f"{cand}.md")
                if not os.path.exists(cand_path):
                    chosen = (cand, cand_path)
                    break
                if _same_logical_entity(cand_path, entity):
                    return cand_path, False
                n += 1
        slug, path = chosen
    src_rel = os.path.relpath(source_path, vault).replace("\\", "/")
    sh = hashlib.sha256(f"{src_rel}\n{slug}\n{entity.get('identifier','')}"
                        .encode("utf-8")).hexdigest()[:12]

    fm_lines = [
        "---",
        f"slug: {slug}",
        "type: collection",
        f"subtype: {entity['subtype']}",
        f"title: {_yaml_quote(entity['title'])}",
        "status: compiled",
        "confidence: 0.95",
        f"created: {today}",
        "sources:",
        f"  - {src_rel}",
        f"sources_hash: {sh}",
        f"compiled_from_hash: {sh}",
    ]
    if entity.get("identifier"):
        ident = entity["identifier"]
        fm_lines.append(f"identifier: {_yaml_quote(ident) if isinstance(ident, str) else ident}")
    for k in ("platform", "year", "author", "genre", "rating",
              "hours_played", "runtime_minutes", "last_played", "date_read"):
        v = entity.get(k)
        if v is not None and v != "":
            fm_lines.append(f"{k}: {_yaml_quote(v) if isinstance(v, str) else v}")
    # `import_note` lives in frontmatter so this importer honors the ownership
    # model: only the LLM compile pass writes prose to entity bodies. Catalog
    # entities are bookkeeping records; nothing meaningful to compile from a
    # library export row, so the body stays empty.
    note = f"{entity['title']} imported from {entity.get('platform', 'export')}"
    fm_lines.append(f"import_note: {_yaml_quote(note)}")
    fm_lines.append("---")
    fm_lines.append("")
    fm_lines.append("<!-- LINKS:BEGIN -->")
    fm_lines.append("<!-- LINKS:END -->")
    fm_lines.append("")

    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write("\n".join(fm_lines))
        f.flush(); os.fsync(f.fileno())
    os.replace(tmp, path)
    return path, True


def _yaml_quote(s):
    """Quote a scalar whenever yaml.safe_load would NOT read the plain
    emission back as the identical string — covering structural misparses
    (`:`, `#`, leading `-`, flow chars, embedded newlines: CSV fields legally
    contain them) AND silent retyping ("1984" -> int, "no" -> False,
    "0234" -> octal 156). json.dumps escapes embedded quotes, backslashes,
    newlines and tabs. Aligned with ingest._yaml_str — same rule in both
    writers."""
    s = str(s)
    try:
        if yaml.safe_load("k: " + s) == {"k": s}:
            return s
    except yaml.YAMLError:
        pass
    return json.dumps(s)


def _read_frontmatter(path):
    try:
        text = open(path, encoding="utf-8").read()
    except OSError:
        return {}
    m = re.match(r"^---\n(.*?)\n---", text, re.S)
    if not m:
        return {}
    try:
        return yaml.safe_load(m.group(1)) or {}
    except yaml.YAMLError:
        return {}


def _same_logical_entity(path, entity):
    """True when the file at `path` already records this export row. With an
    identifier on both sides the identifier decides; with NO identifier on
    either side, fall back to title + subtype so identifier-less re-imports
    (generic CSVs, Kindle rows without ASIN) stay idempotent instead of
    minting a duplicate on every run. Mixed (one side has an identifier,
    the other doesn't) is treated as different — safer to disambiguate."""
    fm = _read_frontmatter(path)
    existing_id, new_id = fm.get("identifier"), entity.get("identifier")
    if existing_id and new_id:
        return str(existing_id) == str(new_id)
    if existing_id or new_id:
        return False
    return (str(fm.get("title", "")) == str(entity.get("title", "")) and
            str(fm.get("subtype", "")) == str(entity.get("subtype", "")))


# ============================================================================
# Driver
# ============================================================================
def import_file(vault, path, dry_run=False):
    # Lock per file: import_dir loops this, so there's no nested acquisition.
    with vault_lock(vault):
        return _import_file_locked(vault, path, dry_run)


def _import_file_locked(vault, path, dry_run=False):
    headers, rows = read_rows(path)
    fmt = detect_format(headers)
    if fmt == "unknown":
        return {"path": path, "format": "unknown", "imported": 0, "skipped": 0,
                "reason": f"could not detect format from headers {headers!r}"}

    valid_subtypes = collection_subtypes(vault)
    handler = FORMAT_HANDLERS[fmt]
    today = datetime.date.today().isoformat()

    imported, skipped, bad = 0, 0, []
    for i, row in enumerate(rows):
        try:
            ent = handler(row)
        except (KeyError, ValueError, TypeError, AttributeError) as e:
            # AttributeError: a non-dict JSON row (e.g. a bare string in
            # "items") must be recorded as one bad row, not crash the file.
            bad.append({"row": i, "error": str(e)})
            continue
        if not ent:
            bad.append({"row": i, "error": "missing required fields (title/subtype)"})
            continue
        # Normalize subtype case: a generic CSV column saying "Game" should
        # match schema's "game" rather than getting flagged as invalid.
        ent["subtype"] = str(ent["subtype"]).lower().strip()
        if ent["subtype"] not in valid_subtypes:
            bad.append({"row": i, "error": f"subtype {ent['subtype']!r} not in "
                                          f"schema (valid: {sorted(valid_subtypes)})"})
            continue
        if dry_run:
            imported += 1
            continue
        _path, created = write_entity(vault, ent, path, today)
        if created:
            imported += 1
        else:
            skipped += 1

    record = {"ts": datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds"),
              "path": os.path.relpath(path, vault).replace("\\", "/"),
              "format": fmt, "rows": len(rows),
              "imported": imported, "skipped": skipped, "bad": bad}

    if not dry_run:
        log_path = os.path.join(vault, IMPORTS_LOG)
        os.makedirs(os.path.dirname(log_path), exist_ok=True)
        with open(log_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(record) + "\n")
    return record


def _refresh_index(vault):
    """Rebuild _index.json in-process (same approach as ingest.refresh_index)
    so newly imported entities are visible to `synapse` without waiting for
    the next daily ingest."""
    try:
        from . import build_index
        saved = sys.argv[:]
        sys.argv = ["build_index.py", vault]
        try:
            build_index.main()
        finally:
            sys.argv = saved
    except Exception as e:
        print(f"warn: build_index failed: {e}", file=sys.stderr)


def import_dir(vault, dir_path, dry_run=False):
    out = []
    for root, _, files in os.walk(dir_path):
        for fn in sorted(files):
            if fn.startswith("_") or fn.startswith("."):
                continue
            if not fn.lower().endswith((".csv", ".json")):
                continue
            path = os.path.join(root, fn)
            try:
                out.append(import_file(vault, path, dry_run=dry_run))
            except Exception as e:
                # Poison-file isolation: one unreadable/malformed export must
                # not abort the whole directory walk — record it and move on.
                out.append({"path": path, "format": "error", "imported": 0,
                            "skipped": 0, "bad": [],
                            "reason": f"{type(e).__name__}: {e}"})
    return out


def main():
    args = sys.argv[1:]
    vault = "."
    source = None
    dry_run = False
    i = 0
    while i < len(args):
        a = args[i]
        if a in ("-h", "--help"):
            print(__doc__); return 0
        if a == "--source" and i + 1 < len(args):
            source = args[i + 1]; i += 2; continue
        if a == "--dry-run":
            dry_run = True
        elif not a.startswith("-"):
            vault = a
        i += 1

    if not source:
        source = os.path.join("raw", "collections")
    # A relative --source is vault-relative FIRST (that's the documented
    # contract); only fall back to a cwd-relative path when the vault-relative
    # one doesn't exist.
    if os.path.isabs(source):
        src = source
    else:
        vault_rel = os.path.join(vault, source)
        src = vault_rel if os.path.exists(vault_rel) else source

    if os.path.isdir(src):
        results = import_dir(vault, src, dry_run=dry_run)
    elif os.path.isfile(src):
        results = [import_file(vault, src, dry_run=dry_run)]
    else:
        sys.exit(f"no such file or directory: {src}")

    total_imp = sum(r["imported"] for r in results)
    if total_imp and not dry_run:
        _refresh_index(vault)  # imported entities are queryable immediately
    total_skip = sum(r["skipped"] for r in results)
    total_bad = sum(len(r.get("bad") or []) for r in results)
    print(f"collections import{' (dry run)' if dry_run else ''}: "
          f"{len(results)} file(s), {total_imp} new, "
          f"{total_skip} skipped, {total_bad} bad row(s)")
    for r in results:
        if r["format"] in ("unknown", "error"):
            print(f"  {r['path']}: SKIP — {r['reason']}")
            continue
        print(f"  {r['path']:60} fmt={r['format']:<10}  +{r['imported']}  "
              f"={r['skipped']}  bad={len(r.get('bad') or [])}")
        for b in (r.get("bad") or [])[:3]:
            print(f"      row {b['row']}: {b['error']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python3
"""
synapse â€” the retrieval layer. Pure Python, NO LLM, no network.

Reads _index.json (built by build_index.py) and answers questions by FILTERING,
never by reasoning. This is the whole point: no model = no hallucination surface.

Commands:
    synapse find <text>        fuzzy search across title/slug/aliases/tags/type
    synapse show <slug>        full record for one entity (resolves the .md file)
    synapse due [--days N]      things with a `due` date within N days (default 30)
    synapse expiring [--days N] things with an `expires`/`renews` date within N days (default 90)
    synapse creds <slug>        show the credential_ref for an entity (the REFERENCE, not the secret)
    synapse resolve <slug>      resolve that reference to the actual SECRET, on demand (prints to stdout)
    synapse list [type]         list everything, or everything of one type
    synapse compact [--apply]   bound the append-only discovery logs (dry-run unless --apply)

`creds` shows only the reference + which backend it routes to. `resolve` actually
fetches the secret via the resolver registry (registry/resolvers.yaml + the
resolvers/ package). The plaintext is printed once and never stored in the vault.

Usage:  synapse <command> [args]   (run from vault dir, or set AGENT_VAULT_PATH)
"""
import sys, os, re, json, datetime

# Force UTF-8 stdout/stderr so entity titles with Unicode don't crash on
# Windows consoles that default to cp1252.
for _s in (sys.stdout, sys.stderr):
    if hasattr(_s, "reconfigure"):
        _s.reconfigure(encoding="utf-8")

VAULT = os.environ.get("AGENT_VAULT_PATH", ".")
FM_RE = re.compile(r"^---\n(.*?)\n---\n(.*)$", re.S)


def load_index():
    p = os.path.join(VAULT, "_index.json")
    if not os.path.exists(p):
        sys.exit("no _index.json â€” run build_index.py first")
    return json.load(open(p, encoding="utf-8"))["entities"]


def load_aliases():
    try:
        import yaml
    except ImportError:
        return {}
    p = os.path.join(VAULT, "registry", "aliases.yaml")
    if not os.path.exists(p):
        return {}
    try:
        with open(p, encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
    except (yaml.YAMLError, OSError) as e:
        print(f"warning: could not read {p}: {e}", file=sys.stderr)
        return {}
    return {k.lower(): v for k, v in (data.get("aliases") or {}).items()}


def norm(s):
    return re.sub(r"\s+", " ", str(s).strip().lower())


def resolve_query_to_slug(q, ents, aliases):
    """Map a free-text query to a slug via aliases, exact match, then substring."""
    nq = norm(q)
    if nq in aliases:
        return aliases[nq]
    for e in ents:
        if nq == e.get("slug", "").lower() or nq in e.get("_match", []):
            return e["slug"]
    return None


def days_until(date_str):
    try:
        d = datetime.date.fromisoformat(str(date_str))
        return (d - datetime.date.today()).days
    except ValueError:
        return None


def cmd_find(args, ents, aliases):
    q = norm(" ".join(args))
    if not q:
        sys.exit("usage: synapse find <text>")
    hits = []
    for e in ents:
        hay = " ".join([e.get("slug", ""), e.get("title", ""), e.get("type", ""),
                        e.get("subtype", ""), " ".join(e.get("tags", [])),
                        " ".join(e.get("_match", []))]).lower()
        if q in hay:
            hits.append(e)
    if not hits:
        print(f"no matches for '{q}'")
        return
    for e in hits:
        print(f"  {e['slug']:30}  {e.get('type','')}/{e.get('subtype','')}  â€” {e.get('title','')}")
    print(f"\n{len(hits)} match(es)")


def cmd_show(args, ents, aliases):
    if not args:
        sys.exit("usage: synapse show <slug>")
    slug = resolve_query_to_slug(" ".join(args), ents, aliases)
    e = next((x for x in ents if x["slug"] == slug), None)
    if not e:
        print(f"no entity for '{' '.join(args)}'")
        return
    path = os.path.join(VAULT, e["path"])
    print(open(path, encoding="utf-8").read())


def _within(ents, field, days, label):
    rows = []
    for e in ents:
        d = (e.get("dates") or {}).get(field)
        if not d:
            continue
        n = days_until(d)
        if n is not None and n <= days:
            rows.append((n, e, d))
    rows.sort(key=lambda r: r[0])
    if not rows:
        print(f"nothing {label} within {days} days")
        return
    for n, e, d in rows:
        when = "OVERDUE" if n < 0 else f"in {n}d"
        print(f"  {d}  ({when:>8})  {e.get('title','')}  [{e['slug']}]")
    print(f"\n{len(rows)} item(s)")


def cmd_due(args, ents, aliases):
    days = _opt_days(args, 30)
    _within(ents, "due", days, "due")


def cmd_expiring(args, ents, aliases):
    days = _opt_days(args, 90)
    # expiring covers both `expires` and `renews`
    rows = []
    for e in ents:
        for field in ("expires", "renews"):
            d = (e.get("dates") or {}).get(field)
            if not d:
                continue
            n = days_until(d)
            if n is not None and n <= days:
                rows.append((n, e, d, field))
    rows.sort(key=lambda r: r[0])
    if not rows:
        print(f"nothing expiring within {days} days")
        return
    for n, e, d, field in rows:
        when = "EXPIRED" if n < 0 else f"in {n}d"
        print(f"  {d}  ({when:>8})  {field:8} {e.get('title','')}  [{e['slug']}]")
    print(f"\n{len(rows)} item(s)")


def cmd_creds(args, ents, aliases):
    if not args:
        sys.exit("usage: synapse creds <slug>")
    slug = resolve_query_to_slug(" ".join(args), ents, aliases)
    e = next((x for x in ents if x["slug"] == slug), None)
    if not e:
        print(f"no entity for '{' '.join(args)}'")
        return
    if not e.get("has_credential"):
        print(f"{e['slug']} has no credential_ref")
        return
    # read the ref from the file (index only flags presence, never stores the ref text)
    ref = _read_credential_ref(e) or "(unreadable)"
    scheme = ref.split("://", 1)[0] if "://" in ref else "(none)"
    print(f"{e['title']}")
    print(f"  reference : {ref}")
    print(f"  backend   : {scheme}  (resolve via registry/resolvers.yaml)")
    print(f"  note      : this is the REFERENCE only; the secret is never stored in the vault")
    print(f"  resolve   : run `synapse resolve {e['slug']}` to fetch the actual secret")


def _read_credential_ref(e):
    """Read the credential_ref string straight from the entity file (the index
    only flags presence, never stores the ref text)."""
    raw = open(os.path.join(VAULT, e["path"]), encoding="utf-8").read()
    m = re.search(r"credential_ref:\s*(\S+)", raw)
    return m.group(1) if m else None


def cmd_resolve(args, ents, aliases):
    """Resolve an entity's credential_ref to its plaintext secret, on demand.

    The secret is the ONLY thing written to stdout (so it pipes cleanly); all
    human-facing context goes to stderr. Nothing is persisted â€” resolution is a
    read of the external secret store, never a write back into the vault."""
    if not args:
        sys.exit("usage: synapse resolve <slug>")
    slug = resolve_query_to_slug(" ".join(args), ents, aliases)
    e = next((x for x in ents if x["slug"] == slug), None)
    if not e:
        print(f"no entity for '{' '.join(args)}'", file=sys.stderr)
        return
    if not e.get("has_credential"):
        print(f"{e['slug']} has no credential_ref", file=sys.stderr)
        return
    ref = _read_credential_ref(e)
    if not ref:
        print(f"{e['slug']}: credential_ref is unreadable", file=sys.stderr)
        return

    # Import the resolver package from the vault (it lives at <vault>/resolvers).
    vpath = os.path.abspath(VAULT)
    if vpath not in sys.path:
        sys.path.insert(0, vpath)
    try:
        import resolvers
    except ImportError as ex:
        sys.exit(f"resolver package unavailable ({ex}); cannot resolve {ref}")

    try:
        secret = resolvers.resolve(ref, VAULT)
    except resolvers.ResolverError as ex:
        print(f"could not resolve {ref}: {ex}", file=sys.stderr)
        sys.exit(1)

    sys.stderr.write(f"# {e.get('title', e['slug'])}  <-  {ref}\n")
    sys.stderr.write("# secret below; never stored in the vault â€” handle with care\n")
    sys.stdout.write(secret + "\n")


def cmd_list(args, ents, aliases):
    want = args[0] if args else None
    rows = [e for e in ents if (not want or e.get("type") == want)]
    for e in sorted(rows, key=lambda x: (x.get("type", ""), x.get("title", ""))):
        print(f"  {e.get('type',''):22} {e.get('subtype',''):14} {e.get('title','')}  [{e['slug']}]")
    print(f"\n{len(rows)} entit{'y' if len(rows)==1 else 'ies'}")


def cmd_compact(args, ents, aliases):
    """Bound the append-only discovery logs (proposals/promoted) without changing
    any promotion outcome. Dry-run unless --apply is passed."""
    sys.path.insert(0, os.path.abspath(VAULT))
    import compact
    apply = "--apply" in args
    results = compact.compact_vault(VAULT, apply=apply)
    saved = 0
    for rel, r in results.items():
        d = r["before"] - r["after"]
        saved += d
        print(f"  {rel:32} {r['before']:6} -> {r['after']:6}  ({d} removed)")
    print(f"{'compacted' if apply else 'dry-run'}: {saved} record(s) "
          f"{'removed (.bak written)' if apply else 'would be removed â€” pass --apply'}")


def _opt_days(args, default):
    if "--days" in args:
        i = args.index("--days")
        try:
            return int(args[i + 1])
        except (IndexError, ValueError):
            sys.exit("--days needs a number")
    return default


COMMANDS = {
    "find": cmd_find, "show": cmd_show, "due": cmd_due,
    "expiring": cmd_expiring, "creds": cmd_creds, "resolve": cmd_resolve,
    "list": cmd_list, "compact": cmd_compact,
}


def main():
    if len(sys.argv) >= 2 and sys.argv[1] in ("-h", "--help", "help"):
        print(__doc__); return 0
    if len(sys.argv) < 2 or sys.argv[1] not in COMMANDS:
        print(__doc__)
        return 1
    ents = load_index()
    aliases = load_aliases()
    COMMANDS[sys.argv[1]](sys.argv[2:], ents, aliases)
    return 0


if __name__ == "__main__":
    sys.exit(main())

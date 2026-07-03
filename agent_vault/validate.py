#!/usr/bin/env python3
"""
validate.py — check every entity file against registry/schema.yaml.

Stage 0 deliverable. Also the smoke-test reused by every later stage.
Pure stdlib + pyyaml. No LLM, no network.

Usage:
    python -m agent_vault.validate [VAULT_DIR]      # defaults to current dir
Exit code 0 = all valid, 1 = problems found.
"""
import sys
import os
import re
import glob

# Force UTF-8 stdout/stderr so entity titles with Unicode don't crash on
# Windows consoles that default to cp1252.
for _s in (sys.stdout, sys.stderr):
    if hasattr(_s, "reconfigure"):
        _s.reconfigure(encoding="utf-8")

try:
    import yaml
except ImportError:
    sys.exit("pyyaml required:  pip install pyyaml --break-system-packages")

REQUIRED = ["slug", "type", "subtype", "title", "status", "confidence", "created",
            "sources", "sources_hash"]
VALID_STATUS = {"stub", "compiled", "needs-review", "unknown", "archived"}
SLUG_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
FM_RE = re.compile(r"^---\n(.*?)\n---\n(.*)$", re.S)
LINKS_RE = re.compile(r"<!-- LINKS:BEGIN -->.*?<!-- LINKS:END -->", re.S)
# Field-name shape for registry/field_mappings.yaml entries. Mirrors
# promote.py's _FIELD_RE (§5) so validate and promote agree.
FIELD_RE = re.compile(r"^[a-z0-9][a-z0-9_]{0,30}$")


def load_schema(vault):
    with open(os.path.join(vault, "registry", "schema.yaml")) as f:
        return yaml.safe_load(f)


_secret_scan = None
_secret_scan_tried = False


def _secret_scanner(vault):
    """Lazily import the (optional) secret_scan module from the vault, once.
    Returns the module or None. Lets validate enforce spec §7 — no plaintext
    secret in any frontmatter value — without a hard import dependency."""
    global _secret_scan, _secret_scan_tried
    if not _secret_scan_tried:
        _secret_scan_tried = True
        try:
            sys.path.insert(0, os.path.abspath(vault))
            import secret_scan as _m
            _secret_scan = _m
        except Exception:
            _secret_scan = None
    return _secret_scan


def _flatten_strings(v):
    """Yield every string nested in a YAML scalar/list/dict value."""
    if isinstance(v, str):
        yield v
    elif isinstance(v, list):
        for x in v:
            yield from _flatten_strings(x)
    elif isinstance(v, dict):
        for x in v.values():
            yield from _flatten_strings(x)


def validate_file(path, schema, vault):
    """Returns (errors, warnings). Errors cause rc=1; warnings are informational."""
    errs = []
    warns = []
    raw = open(path, encoding="utf-8").read()
    m = FM_RE.match(raw)
    if not m:
        return ["no parseable frontmatter block"], []
    body = m.group(2)
    try:
        fm = yaml.safe_load(m.group(1)) or {}
    except yaml.YAMLError as e:
        return [f"frontmatter is not valid YAML: {e}"], []

    # required fields present
    for k in REQUIRED:
        if k not in fm:
            errs.append(f"missing required field: {k}")

    # type / subtype valid against registry
    types = schema.get("types", {})
    t = fm.get("type")
    if t and t not in types:
        errs.append(f"type '{t}' not in registry")
    elif t:
        st = fm.get("subtype")
        valid_subs = types[t].get("subtypes", [])
        if st and st not in valid_subs:
            errs.append(f"subtype '{st}' not valid for type '{t}' (valid: {valid_subs})")

    # status
    if fm.get("status") and fm["status"] not in VALID_STATUS:
        errs.append(f"status '{fm['status']}' not one of {sorted(VALID_STATUS)}")

    # slug shape + matches filename
    slug = fm.get("slug", "")
    if slug and not SLUG_RE.match(slug):
        errs.append(f"slug '{slug}' must be lowercase-dashed")
    fname = os.path.splitext(os.path.basename(path))[0]
    if slug and slug != fname:
        errs.append(f"slug '{slug}' != filename '{fname}'")

    # tags must exist in registry
    reg_tags = set((schema.get("tags") or {}).keys())
    for tag in (fm.get("tags") or []):
        if tag not in reg_tags:
            errs.append(f"tag '{tag}' not yet in registry (ok during seeding; promote it)")

    # confidence range
    c = fm.get("confidence")
    if c is not None and not (isinstance(c, (int, float)) and 0.0 <= c <= 1.0):
        errs.append(f"confidence '{c}' must be a number 0.0–1.0")

    # link block present
    if not LINKS_RE.search(raw):
        errs.append("missing LINKS:BEGIN/END block")

    # credential_ref must not look like a plaintext secret
    cref = str(fm.get("credential_ref", ""))
    if cref and "://" not in cref:
        errs.append(f"credential_ref '{cref}' must be a scheme://store/path reference, never plaintext")

    # spec §7: no frontmatter VALUE may be a plaintext secret. credential_ref is
    # a reference (already checked above), so it's exempt from this scan.
    scanner = _secret_scanner(vault)
    if scanner is not None:
        for k, v in fm.items():
            if k == "credential_ref":
                continue
            for sval in _flatten_strings(v):
                kind = scanner.looks_like_secret(sval)
                if kind:
                    errs.append(f"frontmatter field '{k}' looks like a plaintext "
                                f"secret ({kind}); reference it via credential_ref, "
                                f"never store the secret")
                    break
        # Also scan the RAW frontmatter lines (comments included) — a secret in a
        # `# notes:` comment is invisible to yaml.safe_load above.
        for line in m.group(1).splitlines():
            ls = line.strip()
            if not ls.startswith("#"):
                continue
            kind = scanner.looks_like_secret(ls)
            if kind:
                errs.append(f"frontmatter comment line looks like a plaintext "
                            f"secret ({kind}); never write a secret into the file")
                break

    # stub discipline: a stub should have an empty prose body
    prose = LINKS_RE.sub("", body).strip()
    prose = re.sub(r"<!--.*?-->", "", prose, flags=re.S).strip()
    if fm.get("status") == "stub" and prose:
        errs.append("status is 'stub' but prose body is non-empty (stubs carry no prose)")

    # related refs must resolve to existing entity files
    # warning not error: forward refs are normal during construction
    for ref in (fm.get("related") or []):
        ref = str(ref)
        if "/" not in ref:
            errs.append(f"related ref '{ref}' must be type/slug form")
            continue
        rtype, rslug = ref.split("/", 1)
        target = os.path.join(vault, "entities", rtype, f"{rslug}.md")
        if not os.path.exists(target):
            warns.append(f"unresolved related ref: '{ref}' (entity not yet created)")

    # source files must exist under the vault (strip #fragment from email attachment refs)
    # error for stubs (just ingested, file must be present); warning for compiled/archived
    # (hand-crafted entries may reference real NAS files absent from the test set)
    is_stub = fm.get("status") in ("stub", "needs-review", "unknown")
    for src in (fm.get("sources") or []):
        src_file = str(src).split("#")[0]
        src_path = os.path.join(vault, src_file)
        if not os.path.exists(src_path):
            if is_stub:
                errs.append(f"missing source file: '{src}'")
            else:
                warns.append(f"missing source file: '{src}' (may exist on real NAS)")

    return errs, warns


# ============================================================================
# Registry file validation (field_mappings.yaml + patterns.yaml)
# ============================================================================
def validate_field_mappings(vault):
    """Validate registry/field_mappings.yaml if present. Returns a list of
    error strings (same style as entity errors). Deterministic, pure-stdlib
    + pyyaml: version:1, mappings is a list, each entry has id+pattern+field,
    pattern compiles, field is a valid slug, no secret-shaped EXAMPLE values.
    Mirrors the promote.py deterministic gate (§5) at the validate level."""
    path = os.path.join(vault, "registry", "field_mappings.yaml")
    if not os.path.exists(path):
        return []
    errs = []
    rel = os.path.relpath(path, vault)
    try:
        doc = yaml.safe_load(open(path, encoding="utf-8")) or {}
    except yaml.YAMLError as e:
        return [f"{rel}: invalid YAML: {e}"]
    if not isinstance(doc, dict):
        return [f"{rel}: top-level is not a mapping"]
    ver = doc.get("version")
    if ver != 1:
        errs.append(f"{rel}: version must be 1, got {ver!r}")
    mappings = doc.get("mappings")
    if mappings is None:
        errs.append(f"{rel}: missing 'mappings' list")
        mappings = []
    if not isinstance(mappings, list):
        errs.append(f"{rel}: 'mappings' must be a list, got {type(mappings).__name__}")
        return errs
    scanner = _secret_scanner(vault)
    for i, m in enumerate(mappings):
        if not isinstance(m, dict):
            errs.append(f"{rel}: mappings[{i}] is not a mapping")
            continue
        mid = m.get("id")
        if not mid or not isinstance(mid, str):
            errs.append(f"{rel}: mappings[{i}] missing 'id'")
        elif not re.match(r"^[a-z0-9]+(?:-[a-z0-9]+)*$", mid):
            errs.append(f"{rel}: mappings[{i}] id {mid!r} is not a valid slug")
        pattern = m.get("pattern")
        if not pattern or not isinstance(pattern, str):
            errs.append(f"{rel}: mappings[{i}] ({mid}) missing 'pattern'")
        else:
            try:
                re.compile(pattern)
            except re.error as e:
                errs.append(f"{rel}: mappings[{i}] ({mid}) pattern does not compile: {e}")
            # ReDoS guard: reject nested quantifiers (same check as promote.py)
            try:
                from re import _parser as _rp  # type: ignore[attr-defined]
            except ImportError:
                try:
                    import sre_parse as _rp  # type: ignore[import-not-found,deprecated]
                except ImportError:
                    _rp = None
            if _rp is not None:
                try:
                    ast = _rp.parse(pattern)
                    if _ast_has_nested_quantifier(ast):
                        errs.append(f"{rel}: mappings[{i}] ({mid}) pattern has a "
                                    f"nested quantifier (ReDoS risk)")
                except Exception:
                    pass
        field = m.get("field")
        if not field or not isinstance(field, str):
            errs.append(f"{rel}: mappings[{i}] ({mid}) missing 'field'")
        elif not FIELD_RE.match(field):
            errs.append(f"{rel}: mappings[{i}] ({mid}) field {field!r} "
                        f"must match ^[a-z0-9][a-z0-9_]{{0,30}}$")
        # secret-scan: no stored value should look like a plaintext secret.
        # field_mappings.yaml stores regex patterns (not captured secrets),
        # but a pattern's text could accidentally embed one; scan all string
        # values defensively (same posture as entity frontmatter §7).
        if scanner is not None:
            for k, v in m.items():
                if k in ("id", "field", "parse", "promoted"):
                    continue
                for sval in _flatten_strings(v):
                    kind = scanner.looks_like_secret(sval)
                    if kind:
                        errs.append(f"{rel}: mappings[{i}] ({mid}) field '{k}' "
                                    f"looks like a plaintext secret ({kind})")
                        break
    return errs


def _ast_has_nested_quantifier(node, inside_repeat=False):
    """Walk the regex AST; return True if a quantified group contains another
    quantifier — the classic super-linear ReDoS shape. Mirrors promote.py's
    _ast_has_nested_quantifier exactly."""
    data = getattr(node, "data", None) or []
    for op, args in data:
        opname = getattr(op, "name", str(op))
        if opname in ("MAX_REPEAT", "MIN_REPEAT"):
            if inside_repeat:
                return True
            child = args[2] if isinstance(args, tuple) and len(args) >= 3 else None
            if child is not None and _ast_has_nested_quantifier(child, inside_repeat=True):
                return True
        elif opname == "SUBPATTERN":
            child = args[-1] if isinstance(args, tuple) and args else None
            if child is not None and _ast_has_nested_quantifier(child, inside_repeat):
                return True
        elif opname == "BRANCH":
            branches = args[1] if isinstance(args, tuple) and len(args) >= 2 else []
            for b in (branches or []):
                if _ast_has_nested_quantifier(b, inside_repeat):
                    return True
    return False


def validate_patterns(vault):
    """Validate registry/patterns.yaml billers/shapes remain well-formed.
    promote.py appends to these blocks; validate ensures each entry has the
    documented minimum fields and compiles/parses correctly. Returns error
    strings in the same style as entity errors."""
    path = os.path.join(vault, "registry", "patterns.yaml")
    if not os.path.exists(path):
        return []
    errs = []
    rel = os.path.relpath(path, vault)
    try:
        doc = yaml.safe_load(open(path, encoding="utf-8")) or {}
    except yaml.YAMLError as e:
        return [f"{rel}: invalid YAML: {e}"]
    if not isinstance(doc, dict):
        return [f"{rel}: top-level is not a mapping"]
    seen_ids = set()
    for b in (doc.get("billers") or []):
        if not isinstance(b, dict):
            errs.append(f"{rel}: billers entry is not a mapping")
            continue
        bid = b.get("id")
        if not bid:
            errs.append(f"{rel}: biller entry missing 'id'")
        elif bid in seen_ids:
            errs.append(f"{rel}: duplicate biller id {bid!r}")
        else:
            seen_ids.add(bid)
        matches = b.get("matches")
        if matches is not None and not isinstance(matches, list):
            errs.append(f"{rel}: biller {bid!r} 'matches' must be a list")
    for s in (doc.get("shapes") or []):
        if not isinstance(s, dict):
            errs.append(f"{rel}: shapes entry is not a mapping")
            continue
        sid = s.get("id")
        if not sid:
            errs.append(f"{rel}: shape entry missing 'id'")
        keywords = s.get("keywords")
        if keywords is not None and not isinstance(keywords, list):
            errs.append(f"{rel}: shape {sid!r} 'keywords' must be a list")
    return errs


def main():
    args = sys.argv[1:]
    if args and args[0] in ("-h", "--help"):
        print(__doc__); return 0
    vault = args[0] if args else "."
    schema = load_schema(vault)
    files = sorted(glob.glob(os.path.join(vault, "entities", "*", "*.md")))
    total_errs = 0
    total_warns = 0
    for path in files:
        errs, warns = validate_file(path, schema, vault)
        rel = os.path.relpath(path, vault)
        if errs or warns:
            total_errs += len(errs)
            total_warns += len(warns)
            label = "FAIL" if errs else "warn"
            print(f"{label}  {rel}")
            for e in errs:
                print(f"        - {e}")
            for w in warns:
                print(f"        ~ {w}")
        else:
            print(f"ok    {rel}")
    # --- registry file validation (field_mappings.yaml + patterns.yaml) ---
    reg_errs = []
    for fn in (validate_field_mappings, validate_patterns):
        for e in fn(vault):
            reg_errs.append(e)
    if reg_errs:
        total_errs += len(reg_errs)
        print()
        for e in reg_errs:
            print(f"FAIL  {e}")
    print(f"\n{len(files)} file(s), {total_errs} error(s), {total_warns} warning(s)")
    return 1 if total_errs else 0


if __name__ == "__main__":
    sys.exit(main())

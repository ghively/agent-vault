#!/usr/bin/env python3
"""migrate.py — schema versioning + migration runner (O8).

`registry/schema.yaml` carries a `version:`. This module is the upgrade path when
that format changes in a backward-incompatible way (a renamed/removed type, a
restructured block): register a migration here, bump `validate.SCHEMA_VERSION`,
and `apply` walks a vault from its current version up to the code's version,
running each step's transform and bumping the stamped version.

Today the current version is 1 and **no migrations are registered** — the value
now is the *gate*: `validate.py` rejects a schema whose version it doesn't
understand (newer code wrote it, or it predates the code) instead of silently
mis-validating. When a v2 ever lands, add `1 -> _migrate_1_to_2` below.

Usage:
  python -m agent_vault.migrate check   [VAULT]     # report version drift
  python -m agent_vault.migrate apply   [VAULT] [--dry-run]
"""
from __future__ import annotations

import os
import re
import sys
from typing import Callable

from agent_vault.validate import SCHEMA_VERSION

try:
    import yaml
except ImportError:  # pragma: no cover
    yaml = None  # type: ignore[assignment]

# from_version -> transform(vault_dir) that upgrades the vault to from_version+1.
# Each transform mutates entity/registry files as needed; the version bump itself
# is handled by _set_version() after the transform succeeds.
MIGRATIONS: dict[int, Callable[[str], None]] = {}

_VERSION_LINE_RE = re.compile(r"^version:[ \t]*\d+[^\n]*$", re.M)


def _schema_path(vault: str) -> str:
    return os.path.join(vault, "registry", "schema.yaml")


def current_version(vault: str) -> int | None:
    """Read the stamped schema version, or None if missing/unreadable."""
    path = _schema_path(vault)
    if not os.path.exists(path) or yaml is None:
        return None
    try:
        with open(path, encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
    except (OSError, yaml.YAMLError):
        return None
    v = data.get("version") if isinstance(data, dict) else None
    return v if isinstance(v, int) else None


def _set_version(vault: str, version: int) -> None:
    """Rewrite the `version:` line in schema.yaml, preserving comments."""
    path = _schema_path(vault)
    with open(path, encoding="utf-8") as f:
        text = f.read()
    if _VERSION_LINE_RE.search(text):
        new = _VERSION_LINE_RE.sub(f"version: {version}", text, count=1)
    else:
        new = f"version: {version}\n" + text
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(new)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)


def check(vault: str) -> dict[str, object]:
    """Report the vault's schema version vs the code's expected version."""
    cur = current_version(vault)
    if cur is None:
        status = "missing"
    elif cur == SCHEMA_VERSION:
        status = "up_to_date"
    elif cur < SCHEMA_VERSION:
        status = "needs_migration"
    else:
        status = "code_too_old"
    return {"current": cur, "expected": SCHEMA_VERSION, "status": status,
            "pending": [v for v in range(cur or 0, SCHEMA_VERSION)
                        if v in MIGRATIONS]}


def apply(vault: str, dry_run: bool = False) -> dict[str, object]:
    """Walk the vault from its current version up to SCHEMA_VERSION."""
    cur = current_version(vault)
    if cur is None:
        raise SystemExit("schema.yaml has no readable version; fix it by hand "
                         "before migrating")
    if cur > SCHEMA_VERSION:
        raise SystemExit(f"schema version {cur} is newer than this code supports "
                         f"({SCHEMA_VERSION}); upgrade agent-vault")
    applied: list[int] = []
    while cur < SCHEMA_VERSION:
        step = MIGRATIONS.get(cur)
        if step is None:
            raise SystemExit(f"no migration registered for version {cur} -> "
                             f"{cur + 1}; cannot upgrade automatically")
        if not dry_run:
            step(vault)
            _set_version(vault, cur + 1)
        applied.append(cur + 1)
        cur += 1
    return {"applied": applied, "final": cur, "dry_run": dry_run}


def main() -> int:
    args = sys.argv[1:]
    if not args or args[0] in ("-h", "--help"):
        print(__doc__)
        return 0
    cmd = args[0]
    rest = args[1:]
    dry_run = "--dry-run" in rest
    positional = [a for a in rest if not a.startswith("--")]
    vault = positional[0] if positional else "."

    if cmd == "check":
        st = check(vault)
        print(f"schema version: current={st['current']} expected={st['expected']} "
              f"-> {st['status']}")
        if st["status"] == "needs_migration" and not st["pending"]:
            print("  (no migration registered for the gap — this is a code/vault "
                  "version mismatch to resolve manually)", file=sys.stderr)
        return 0 if st["status"] in ("up_to_date",) else 1

    if cmd == "apply":
        result = apply(vault, dry_run=dry_run)
        if result["applied"]:
            verb = "would apply" if dry_run else "applied"
            print(f"{verb} migration(s) -> version {result['final']}")
        else:
            print(f"already at version {result['final']}; nothing to do")
        return 0

    print(f"unknown command {cmd!r} (expected: check, apply)", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python3
"""backup.py — vault snapshot / export / restore (O2).

A shared knowledgebase is a system of record; it needs a safety net beyond the
per-file atomic writes. This packages the *authored* state — `entities/`,
`registry/`, `discovery/` (and optionally `raw/`) — into a single portable
`.tar.gz`, and restores it safely.

What is and isn't included:
  - Included: entities/, registry/, discovery/ (the wiki + vocabulary + ledgers).
  - Optional (`--include-raw`): raw/ source documents (large; append-only, so
    often backed up separately).
  - Excluded: derived artifacts (`_index.json`/`_index.md`/`_index.db`/
    `_vectors.db`), the lock file, and any `*.tmp` — all rebuildable via
    `python -m agent_vault.build_index` (and `agent_vault.semantic build`).

Restore is safe: tar members are validated to stay inside the destination (no
path traversal / absolute paths), and it refuses to overwrite a non-empty vault
unless `--force`.

Usage:
  python -m agent_vault.backup export [VAULT] --out vault-backup.tar.gz [--include-raw]
  python -m agent_vault.backup restore <archive.tar.gz> [DEST] [--force]
  python -m agent_vault.backup list <archive.tar.gz>
"""
from __future__ import annotations

import io
import json
import os
import sys
import tarfile
from datetime import datetime, timezone
from typing import Any

# Directories carrying authored state (in restore/backup order).
_CORE_DIRS = ("entities", "registry", "discovery")
_EXCLUDE_NAMES = {"_index.json", "_index.md", "_index.db", "_index.db.tmp",
                  "_vectors.db", "_vectors.db.tmp", "_vault.lock"}
META_NAME = "_export_meta.json"


def _should_include(rel_path: str) -> bool:
    base = os.path.basename(rel_path)
    if base in _EXCLUDE_NAMES or base.endswith(".tmp"):
        return False
    return True


def export_vault(vault: str, out_path: str, include_raw: bool = False) -> dict[str, Any]:
    """Write a .tar.gz snapshot of the vault. Returns a summary dict."""
    dirs = list(_CORE_DIRS) + (["raw"] if include_raw else [])
    counts: dict[str, int] = {}
    tmp = out_path + ".tmp"
    with tarfile.open(tmp, "w:gz") as tar:
        for d in dirs:
            src = os.path.join(vault, d)
            if not os.path.isdir(src):
                continue
            n = 0
            for root, _sub, files in os.walk(src):
                for fn in sorted(files):
                    full = os.path.join(root, fn)
                    rel = os.path.relpath(full, vault).replace("\\", "/")
                    if not _should_include(rel):
                        continue
                    tar.add(full, arcname=rel)
                    n += 1
            counts[d] = n
        meta = {
            "created": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "include_raw": include_raw,
            "counts": counts,
        }
        meta_bytes = json.dumps(meta, indent=2).encode("utf-8")
        info = tarfile.TarInfo(META_NAME)
        info.size = len(meta_bytes)
        tar.addfile(info, io.BytesIO(meta_bytes))
    os.replace(tmp, out_path)
    return {"out": out_path, **meta}


def _safe_members(tar: tarfile.TarFile, dest: str) -> list[tarfile.TarInfo]:
    """Return only members that extract INSIDE dest — reject traversal/absolute
    paths and links (defends restore against a tampered archive)."""
    dest_abs = os.path.realpath(dest)
    safe = []
    for m in tar.getmembers():
        if m.islnk() or m.issym():
            continue
        target = os.path.realpath(os.path.join(dest, m.name))
        if target == dest_abs or target.startswith(dest_abs + os.sep):
            safe.append(m)
        else:
            print(f"warn: skipping unsafe archive member {m.name!r}", file=sys.stderr)
    return safe


def restore_vault(archive: str, dest: str, force: bool = False) -> dict[str, Any]:
    """Extract a snapshot into `dest`. Refuses a non-empty vault unless force."""
    entities = os.path.join(dest, "entities")
    if os.path.isdir(entities) and any(os.scandir(entities)) and not force:
        raise SystemExit(
            f"refusing to restore over a non-empty vault at {dest!r} "
            f"(entities/ is not empty); pass --force to overwrite")
    os.makedirs(dest, exist_ok=True)
    with tarfile.open(archive, "r:gz") as tar:
        members = _safe_members(tar, dest)
        tar.extractall(dest, members=members)  # noqa: S202 - members pre-validated
    # Rebuild derived indexes so the restored vault is immediately queryable.
    try:
        from agent_vault import build_index
        build_index.reindex(dest, quiet=True)  # type: ignore[no-untyped-call]  # legacy untyped
    except Exception as e:  # noqa: BLE001
        print(f"warn: reindex after restore failed ({e}); run "
              f"`python -m agent_vault.build_index {dest}`", file=sys.stderr)
    return {"dest": dest, "members": len(members)}


def _read_meta(archive: str) -> dict[str, Any]:
    with tarfile.open(archive, "r:gz") as tar:
        try:
            f = tar.extractfile(META_NAME)
            if f is not None:
                data: Any = json.loads(f.read().decode("utf-8"))
                return data if isinstance(data, dict) else {}
        except KeyError:
            pass
    return {}


def main() -> int:
    args = sys.argv[1:]
    if not args or args[0] in ("-h", "--help"):
        print(__doc__)
        return 0
    cmd = args[0]
    rest = args[1:]

    if cmd == "export":
        include_raw = "--include-raw" in rest
        out = None
        if "--out" in rest:
            i = rest.index("--out")
            out = rest[i + 1] if i + 1 < len(rest) else None
        positional = [a for a in rest if not a.startswith("--")
                      and a != (out or "")]
        vault = positional[0] if positional else "."
        if not out:
            print("usage: python -m agent_vault.backup export [VAULT] --out FILE "
                  "[--include-raw]", file=sys.stderr)
            return 2
        summary = export_vault(vault, out, include_raw=include_raw)
        print(f"exported {sum(summary['counts'].values())} file(s) -> {out} "
              f"({', '.join(f'{k}:{v}' for k, v in summary['counts'].items())})")
        return 0

    if cmd == "restore":
        force = "--force" in rest
        positional = [a for a in rest if not a.startswith("--")]
        if not positional:
            print("usage: python -m agent_vault.backup restore ARCHIVE [DEST] [--force]",
                  file=sys.stderr)
            return 2
        archive = positional[0]
        dest = positional[1] if len(positional) > 1 else "."
        summary = restore_vault(archive, dest, force=force)
        print(f"restored {summary['members']} file(s) -> {dest}")
        return 0

    if cmd == "list":
        if not rest:
            print("usage: python -m agent_vault.backup list ARCHIVE", file=sys.stderr)
            return 2
        meta = _read_meta(rest[0])
        print(json.dumps(meta, indent=2) if meta else "(no export metadata)")
        return 0

    print(f"unknown command {cmd!r} (expected: export, restore, list)", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main())

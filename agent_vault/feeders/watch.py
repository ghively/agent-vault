#!/usr/bin/env python3
"""
feeders/watch.py — watched-folder ingestion feeder for Agent Vault.

Watches a directory (default: ``<vault>/inbox/``) for new files, copies them
into ``raw/<category>/`` with a timestamped name (honoring the append-only
contract), then runs the existing deterministic ingest pipeline to produce
entity stubs. Stubs are findable immediately via search; the weekly compile
cadence fills in prose later.

Modes:
    --once    Process the inbox once and exit (for cron).
    --watch   Poll the inbox continuously (daemon mode, default interval 30s).

The feeder does NOT call the compiler (that's a separate cadence). It calls
the existing ``agent_vault.ingest.ingest(vault)`` which classifies
deterministically and writes stubs.

Append-only contract: already-ingested files (by SHA-256 hash) are skipped —
the ingest manifest tracks hashes, so a file dropped twice won't produce a
duplicate entity.

Usage:
    python -m agent_vault.feeders.watch --once [vault_dir]
    python -m agent_vault.feeders.watch --watch --interval 60 [vault_dir]
"""
from __future__ import annotations

import argparse
import datetime
import hashlib
import os
import shutil
import sys
import time
from pathlib import Path

# Force UTF-8 stdout/stderr.
for _s in (sys.stdout, sys.stderr):
    if hasattr(_s, "reconfigure"):
        _s.reconfigure(encoding="utf-8")

# Default poll interval for --watch mode (seconds).
DEFAULT_INTERVAL = 30

# Category mapping: file extension -> raw/ subdirectory.
EXT_TO_CATEGORY = {
    ".pdf": "documents",
    ".eml": "email",
    ".jpg": "media",
    ".jpeg": "media",
    ".png": "media",
    ".txt": "misc",
    ".csv": "statements",
    ".docx": "documents",
    ".xlsx": "statements",
    ".html": "documents",
    ".htm": "documents",
}

DEFAULT_CATEGORY = "misc"


def _sha256(path: str) -> str:
    """Compute SHA-256 hash of a file."""
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def _category_for(filename: str) -> str:
    """Map a filename to a raw/ subdirectory by extension."""
    ext = Path(filename).suffix.lower()
    return EXT_TO_CATEGORY.get(ext, DEFAULT_CATEGORY)


def _timestamped_name(filename: str) -> str:
    """Generate a timestamped filename to avoid collisions in raw/.

    'warranty.pdf' -> '20260728-161500-warranty.pdf'
    """
    ts = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    # Sanitize: keep only safe chars, collapse spaces to hyphens.
    safe = filename.replace(" ", "-")
    safe = "".join(c for c in safe if c.isalnum() or c in ".-_")
    return f"{ts}-{safe}"


def process_inbox(vault: str, inbox: str | None = None,
                  dry_run: bool = False) -> dict:
    """Process all files in the inbox once.

    Copies each file to ``raw/<category>/<timestamped-name>`` and then runs
    the ingest pipeline. Returns a summary dict:

        {moved: [(filename, dest, sha256), ...],
         skipped: [(filename, reason), ...],
         ingest: {...}}

    Args:
        vault: Path to the vault root.
        inbox: Path to the inbox directory (default: ``<vault>/inbox/``).
        dry_run: If True, report what would happen without side effects.
    """
    vault_path = Path(vault)
    inbox_path = Path(inbox) if inbox else vault_path / "inbox"

    if not inbox_path.is_dir():
        # Missing inbox is the normal steady state (nothing to process), not
        # an error. Return an empty no-op so cadences don't fail when no files
        # have been dropped yet.
        return {"moved": [], "skipped": [], "ingest": None, "message": "inbox not present (nothing to do)"}

    moved = []
    skipped = []

    files = sorted(f for f in inbox_path.iterdir() if f.is_file() and not f.name.startswith("."))
    if not files:
        return {"moved": moved, "skipped": skipped, "ingest": None, "message": "inbox empty"}

    # Load existing manifest hashes to skip duplicates before copying.
    manifest_path = vault_path / "raw" / "_manifest.jsonl"
    seen_hashes: set[str] = set()
    if manifest_path.exists():
        import json
        for line in manifest_path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                rec = json.loads(line)
                if rec.get("hash"):
                    seen_hashes.add(rec["hash"])
            except (json.JSONDecodeError, KeyError):
                continue

    for f in files:
        try:
            sha = _sha256(str(f))
        except OSError as e:
            skipped.append((f.name, f"read error: {e}"))
            continue

        # Skip if this content is already in the vault (by hash).
        if sha in seen_hashes:
            skipped.append((f.name, "already ingested (hash match)"))
            seen_hashes.add(sha)
            continue

        category = _category_for(f.name)
        dest_name = _timestamped_name(f.name)
        dest_dir = vault_path / "raw" / category
        dest = dest_dir / dest_name

        if dry_run:
            moved.append((f.name, str(dest.relative_to(vault_path)), sha))
            print(f"  [dry-run] {f.name} -> raw/{category}/{dest_name}")
            continue

        try:
            dest_dir.mkdir(parents=True, exist_ok=True)
            shutil.copy2(str(f), str(dest))
            moved.append((f.name, str(dest.relative_to(vault_path)), sha))
            seen_hashes.add(sha)
            print(f"  moved: {f.name} -> raw/{category}/{dest_name}")
        except OSError as e:
            skipped.append((f.name, f"copy error: {e}"))

    if dry_run:
        return {"moved": moved, "skipped": skipped, "ingest": None}

    if not moved:
        return {"moved": moved, "skipped": skipped, "ingest": None,
                "message": "nothing new to ingest"}

    # Run the ingest pipeline on the vault.
    try:
        from agent_vault.ingest import ingest
        result = ingest(str(vault_path))
        print(f"  ingest: {result['new']} new, {result['skipped']} skipped")
        return {"moved": moved, "skipped": skipped, "ingest": result}
    except Exception as e:
        return {"moved": moved, "skipped": skipped,
                "ingest": {"error": str(e)}}


def watch_loop(vault: str, inbox: str | None = None,
               interval: int = DEFAULT_INTERVAL) -> None:
    """Continuously poll the inbox and process new files.

    Runs forever until interrupted (Ctrl-C / SIGTERM).
    """
    vault_path = Path(vault)
    inbox_path = Path(inbox) if inbox else vault_path / "inbox"
    inbox_path.mkdir(parents=True, exist_ok=True)

    print(f"watching {inbox_path} (interval={interval}s) — Ctrl-C to stop",
          file=sys.stderr)
    try:
        while True:
            result = process_inbox(vault, str(inbox_path))
            if result.get("moved"):
                print(f"[{datetime.datetime.now().isoformat(timespec='seconds')}] "
                      f"processed {len(result['moved'])} file(s)", file=sys.stderr)
            time.sleep(interval)
    except KeyboardInterrupt:
        print("\nstopped", file=sys.stderr)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Watched-folder ingestion feeder for Agent Vault.",
        prog="agent-vault-feeder",
    )
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--once", action="store_true", default=True,
                      help="Process inbox once and exit (default, for cron).")
    mode.add_argument("--watch", action="store_true",
                      help="Poll inbox continuously (daemon mode).")
    parser.add_argument("--dry-run", action="store_true",
                        help="Show what would happen without side effects.")
    parser.add_argument("--interval", type=int, default=DEFAULT_INTERVAL,
                        help=f"Poll interval for --watch (seconds, default {DEFAULT_INTERVAL}).")
    parser.add_argument("--inbox", type=str, default=None,
                        help="Inbox directory (default: <vault>/inbox/).")
    parser.add_argument("vault", nargs="?", default=".",
                        help="Vault directory (default: current directory).")
    args = parser.parse_args()

    vault = os.path.abspath(args.vault)
    if not os.path.isdir(os.path.join(vault, "registry")):
        print(f"error: '{vault}' is not an Agent Vault (no registry/ dir)",
              file=sys.stderr)
        return 2

    if args.dry_run:
        result = process_inbox(vault, args.inbox, dry_run=True)
        n = len(result.get("moved", []))
        print(f"\ndry-run: {n} file(s) would be moved, "
              f"{len(result.get('skipped', []))} skipped")
        return 0

    if args.watch:
        watch_loop(vault, args.inbox, args.interval)
        return 0

    # --once (default)
    result = process_inbox(vault, args.inbox)
    if "error" in result:
        print(f"error: {result['error']}", file=sys.stderr)
        return 1
    moved = len(result.get("moved", []))
    skipped = len(result.get("skipped", []))
    if moved == 0 and skipped == 0:
        print("inbox empty — nothing to do")
    else:
        print(f"\nfeeder: {moved} moved, {skipped} skipped")
    ing = result.get("ingest")
    if ing and isinstance(ing, dict):
        if "error" in ing:
            print(f"ingest error: {ing['error']}", file=sys.stderr)
            return 1
        print(f"ingest: {ing.get('new', 0)} new entities, "
              f"{ing.get('skipped', 0)} skipped")
    return 0


if __name__ == "__main__":
    sys.exit(main())

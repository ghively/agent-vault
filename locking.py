#!/usr/bin/env python3
"""
locking.py â€” one vault-wide advisory write lock.

Every process that MUTATES the vault (ingest, compile, promote, reclassify,
collections import) takes this exclusive lock so overlapping runs serialize
instead of racing. Readers (synapse, build_index, lint, validate) do not take it.

Without it, e.g. `reclassify_apply` moving an entity file + rewriting cross-refs
could interleave with a `promote`/`compile` reading the same tree, corrupting
state. `os.replace` makes individual writes atomic, but multi-file sequences
(rename + N ref rewrites + log append) need this coarse lock to be consistent.

POSIX `fcntl.flock`, acquired with LOCK_NB + bounded retry (default 10 minutes,
override via AGENT_VAULT_LOCK_TIMEOUT_S; 0 = wait forever). A bounded wait keeps an
hourly cron `daily.sh` from silently piling up worker processes behind a hung
weekly compile. On platforms without fcntl it degrades to a no-op â€” the
realistic deployment is a single-host home NAS with sequential cadences, where
the lock only matters when two runs accidentally overlap.

A runtime flock failure (e.g. ENOLCK on some NFS mounts â€” plausible on a NAS)
is NOT silently swallowed: we warn loudly on stderr and proceed, so the
operator learns the mutating passes are running unserialized.
"""
import os
import sys
import time

LOCK_NAME = "_vault.lock"
DEFAULT_TIMEOUT_S = 600.0
RETRY_INTERVAL_S = 0.5


class LockTimeout(RuntimeError):
    """Could not acquire the vault lock within the timeout."""


class vault_lock:
    """Context manager: exclusive advisory lock on registry/_vault.lock."""

    def __init__(self, vault, timeout_s=None):
        self.path = os.path.join(vault, "registry", LOCK_NAME)
        self.fh = None
        if timeout_s is None:
            try:
                timeout_s = float(os.environ.get("AGENT_VAULT_LOCK_TIMEOUT_S",
                                                 DEFAULT_TIMEOUT_S))
            except ValueError:
                timeout_s = DEFAULT_TIMEOUT_S
        self.timeout_s = timeout_s

    def __enter__(self):
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        self.fh = open(self.path, "w")
        try:
            import fcntl
        except ImportError:
            return self  # documented no-op on platforms without fcntl
        deadline = (time.monotonic() + self.timeout_s) if self.timeout_s else None
        while True:
            try:
                fcntl.flock(self.fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                return self
            except BlockingIOError:
                if deadline is not None and time.monotonic() >= deadline:
                    self.fh.close()
                    self.fh = None
                    raise LockTimeout(
                        f"could not acquire {self.path} within "
                        f"{self.timeout_s:.0f}s â€” another mutating pass is "
                        f"holding it (set AGENT_VAULT_LOCK_TIMEOUT_S to adjust)")
                time.sleep(RETRY_INTERVAL_S)
            except OSError as e:
                # e.g. ENOLCK on an NFS mount: locking is unavailable, not
                # merely contended. Proceeding unlocked is the documented
                # best-effort posture, but it must never be silent.
                print(f"warn: vault lock unavailable ({e}); proceeding "
                      f"UNLOCKED â€” concurrent mutating runs will not "
                      f"serialize", file=sys.stderr)
                return self

    def __exit__(self, *exc):
        if self.fh is None:
            return
        try:
            import fcntl
            fcntl.flock(self.fh.fileno(), fcntl.LOCK_UN)
        except (ImportError, OSError):
            pass
        self.fh.close()

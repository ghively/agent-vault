#!/usr/bin/env python3
"""
resolvers.pass_store — resolve secrets from the standard Unix password store (`pass`).

Config (registry/resolvers.yaml):

    pass:
      module: resolvers.pass_store
      # store_dir: /home/me/.password-store   # optional; else pass's default / PASSWORD_STORE_DIR

Ref → entry mapping:

    pass://personal/email      ->  pass show personal/email   (first line = the secret)
    pass://banking/bofa/login  ->  pass show banking/bofa/login

`pass` returns the full entry; by convention the **first line** is the password
(subsequent lines are metadata), so we return the first line. Auth is handled by
`pass`/gpg-agent — this resolver just invokes the already-unlocked CLI.
"""
import shutil, subprocess, os
from . import ResolverError, safe_stderr

TIMEOUT_S = 30


def entry_name(ref):
    """Map a parsed ref to the pass entry path 'store/path...' (pure)."""
    return "/".join([ref.store, *ref.path])


def resolve(ref, config):
    binary = shutil.which("pass")
    if not binary:
        raise ResolverError("pass resolver: `pass` CLI not found on PATH")
    env = dict(os.environ)
    store_dir = (config or {}).get("store_dir")
    if store_dir:
        env["PASSWORD_STORE_DIR"] = os.path.expanduser(str(store_dir))
    try:
        proc = subprocess.run([binary, "show", entry_name(ref)],
                              capture_output=True, timeout=TIMEOUT_S, env=env)
    except subprocess.TimeoutExpired:
        raise ResolverError("pass resolver: `pass show` timed out")
    except OSError as e:
        raise ResolverError(f"pass resolver: failed to run pass: {e}")
    if proc.returncode != 0:
        err = safe_stderr(proc.stderr)
        raise ResolverError(f"pass resolver: `pass show` failed (rc={proc.returncode}): {err}")
    out = proc.stdout.decode("utf-8", "replace")
    # First line is the password by pass convention.
    return out.splitlines()[0] if out.strip() else ""

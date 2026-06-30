#!/usr/bin/env python3
"""
resolvers.secret_tool — resolve secrets from the GNOME keyring via `secret-tool`.

Config (registry/resolvers.yaml):

    secret-tool:
      module: resolvers.secret_tool
      # service_attr: service     # attribute name for the first ref segment
      # account_attr: account     # attribute name for the second ref segment

Ref → lookup mapping:

    secret-tool://email/me@example.com  ->  secret-tool lookup service email account me@example.com
    secret-tool://wifi                  ->  secret-tool lookup service wifi

i.e. `secret-tool://<service>[/<account>]`. The attribute names default to
`service`/`account` and are overridable in config. Auth is the desktop session's
unlocked keyring; this resolver just invokes the CLI.
"""
import shutil
import subprocess
from . import ResolverError, safe_stderr

TIMEOUT_S = 15


def lookup_args(ref, config=None):
    """Map a ref to the `secret-tool lookup` attribute/value args (pure)."""
    service_attr = (config or {}).get("service_attr") or "service"
    account_attr = (config or {}).get("account_attr") or "account"
    if len(ref.path) > 1:
        # Silently dropping extra segments could resolve a LESS specific
        # secret than the ref names — refuse instead.
        raise ResolverError(
            "secret-tool resolver: ref must be secret-tool://<service>[/<account>] "
            "(too many path segments)")
    args = ["lookup", service_attr, ref.store]
    if ref.path:
        args += [account_attr, ref.path[0]]
    return args


def resolve(ref, config):
    binary = shutil.which("secret-tool")
    if not binary:
        raise ResolverError("secret-tool resolver: `secret-tool` CLI not found on PATH")
    try:
        proc = subprocess.run([binary, *lookup_args(ref, config)],
                              capture_output=True, timeout=TIMEOUT_S)
    except subprocess.TimeoutExpired:
        raise ResolverError("secret-tool resolver: lookup timed out")
    except OSError as e:
        raise ResolverError(f"secret-tool resolver: failed to run secret-tool: {e}")
    if proc.returncode != 0:
        err = safe_stderr(proc.stderr)
        raise ResolverError(
            f"secret-tool resolver: lookup failed (rc={proc.returncode}): {err or 'not found'}")
    out = proc.stdout.decode("utf-8", "replace")
    return out[:-1] if out.endswith("\n") else out

#!/usr/bin/env python3
"""
resolvers.macos_keychain — resolve secrets from the macOS Keychain via `security`.

Config (registry/resolvers.yaml):

    keychain:
      module: resolvers.macos_keychain

Ref → lookup mapping:

    keychain://github.com/me        ->  security find-generic-password -s github.com -a me -w
    keychain://wifi-home            ->  security find-generic-password -s wifi-home -w

i.e. `keychain://<service>[/<account>]`. Returns the password (`-w`). Auth is the
logged-in user's unlocked keychain; this resolver just invokes the CLI. macOS only.
"""
import shutil, subprocess
from . import ResolverError, safe_stderr

TIMEOUT_S = 15


def security_args(ref):
    """Map a ref to `security find-generic-password` args (pure)."""
    if len(ref.path) > 1:
        # Silently dropping extra segments could resolve a LESS specific
        # secret than the ref names — refuse instead.
        raise ResolverError(
            "keychain resolver: ref must be keychain://<service>[/<account>] "
            "(too many path segments)")
    args = ["find-generic-password", "-s", ref.store]
    if ref.path:
        args += ["-a", ref.path[0]]
    args.append("-w")
    return args


def resolve(ref, config):
    binary = shutil.which("security")
    if not binary:
        raise ResolverError("keychain resolver: `security` CLI not found on PATH (macOS only)")
    try:
        proc = subprocess.run([binary, *security_args(ref)],
                              capture_output=True, timeout=TIMEOUT_S)
    except subprocess.TimeoutExpired:
        raise ResolverError("keychain resolver: lookup timed out")
    except OSError as e:
        raise ResolverError(f"keychain resolver: failed to run security: {e}")
    if proc.returncode != 0:
        err = safe_stderr(proc.stderr)
        raise ResolverError(
            f"keychain resolver: lookup failed (rc={proc.returncode}): {err or 'not found'}")
    out = proc.stdout.decode("utf-8", "replace")
    return out[:-1] if out.endswith("\n") else out

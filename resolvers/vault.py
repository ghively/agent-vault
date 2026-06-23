#!/usr/bin/env python3
"""
resolvers.vault — resolve secrets from HashiCorp Vault via the `vault` CLI.

Config (registry/resolvers.yaml):

    vault:
      module: resolvers.vault
      # default_field: password    # KV field to read when the ref doesn't name one
      # mount: secret              # default KV mount if the ref omits it

Ref → `vault kv get` mapping:

    vault://secret/bofa/password   ->  vault kv get -field=password secret/bofa
    vault://secret/bofa            ->  vault kv get -field=<default_field> secret/bofa

i.e. `vault://<mount>/<path...>[/<field>]`. If the last segment matches the
configured/`password` default field name it is treated as the field; otherwise
`default_field` is used and the whole ref is the KV path. Auth + endpoint come
from the standard `VAULT_ADDR` / `VAULT_TOKEN` environment — this resolver just
invokes the already-authenticated CLI.
"""
import shutil, subprocess
from . import ResolverError, safe_stderr

TIMEOUT_S = 30
_KNOWN_FIELDS = {"password", "username", "value", "token", "key", "secret"}


def kv_target(ref, config=None):
    """Return (kv_path, field) for a ref (pure)."""
    default_field = (config or {}).get("default_field") or "password"
    segments = [ref.store, *ref.path]
    if len(segments) < 2:
        raise ResolverError(
            "vault resolver: ref must be vault://<mount>/<path>[/<field>]")
    # Treat a trailing known-field-name segment as the field selector.
    if len(segments) >= 3 and segments[-1] in _KNOWN_FIELDS:
        field = segments[-1]
        path = "/".join(segments[:-1])
    else:
        field = default_field
        path = "/".join(segments)
    return path, field


def resolve(ref, config):
    binary = shutil.which("vault")
    if not binary:
        raise ResolverError("vault resolver: `vault` CLI not found on PATH")
    kv_path, field = kv_target(ref, config)
    try:
        proc = subprocess.run([binary, "kv", "get", f"-field={field}", kv_path],
                              capture_output=True, timeout=TIMEOUT_S)
    except subprocess.TimeoutExpired:
        raise ResolverError("vault resolver: `vault kv get` timed out")
    except OSError as e:
        raise ResolverError(f"vault resolver: failed to run vault: {e}")
    if proc.returncode != 0:
        err = safe_stderr(proc.stderr)
        raise ResolverError(f"vault resolver: `vault kv get` failed (rc={proc.returncode}): {err}")
    out = proc.stdout.decode("utf-8", "replace")
    return out[:-1] if out.endswith("\n") else out

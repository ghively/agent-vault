#!/usr/bin/env python3
"""
resolvers.bitwarden — resolve secrets from Bitwarden (or a self-hosted
Vaultwarden server) via the `bw` CLI.

Config (registry/resolvers.yaml):

    bitwarden:
      module: resolvers.bitwarden
      # default_field: password       # field used when the ref omits one
      # session_env: BW_SESSION       # env var holding the session token

    # Vaultwarden speaks the same CLI; point `bw config server <url>` at it and
    # route the scheme to this same module:
    vaultwarden:
      module: resolvers.bitwarden

Ref → lookup mapping:

    bitwarden://bofa-login            ->  bw get password "bofa-login"
    bitwarden://bofa-login/username   ->  bw get username "bofa-login"
    bitwarden://bofa-login/pin        ->  custom field "pin" (via `bw get item`)

i.e. `bitwarden://<item>[/<field>]`. With no field it uses `default_field`
(default `password`). Built-in fields (password/username/uri/totp/notes) are
fetched directly; anything else is read as a custom field from the item JSON.

AUTHENTICATION is the operator's responsibility and is NOT handled here: unlock
the vault (`bw unlock`) and export BW_SESSION, or set `session_env: <VAR>` in
config to name an alternative environment variable. This resolver invokes the
already-unlocked CLI. A literal `session:` value in resolvers.yaml is NOT
supported: it would put an unlock secret in plaintext inside the vault tree,
and passing it as `--session` on argv would expose it in /proc/<pid>/cmdline —
the same exposure the gpg backend avoids by using a passphrase fd.
"""
import os
import shutil
import subprocess
import json
from . import ResolverError, safe_stderr

TIMEOUT_S = 30
# Fields `bw get <field> <item>` returns directly. Anything else = custom field.
DIRECT_FIELDS = {"password", "username", "uri", "totp", "notes"}


def plan(ref, config=None):
    """Map a parsed ref to (item, field) (pure)."""
    segments = [ref.store, *ref.path]
    item = segments[0]
    if len(segments) == 1:
        field = (config or {}).get("default_field") or "password"
    elif len(segments) == 2:
        field = segments[1]
    else:
        raise ResolverError(
            "bitwarden resolver: ref must be bitwarden://<item>[/<field>] "
            "(too many path segments)")
    return item, field


def _run(args, config):
    cfg = config or {}
    if cfg.get("session"):
        raise ResolverError(
            "bitwarden resolver: literal `session:` in resolvers.yaml is not "
            "supported (plaintext unlock secret in the vault tree, and argv "
            "exposure via /proc). Export BW_SESSION, or point `session_env:` "
            "at an environment variable.")
    env = None
    session_env = cfg.get("session_env")
    if session_env:
        token = os.environ.get(str(session_env))
        if not token:
            raise ResolverError(
                f"bitwarden resolver: session_env {session_env!r} is set in "
                f"config but that environment variable is empty/unset")
        env = {**os.environ, "BW_SESSION": token}
    try:
        proc = subprocess.run(args, capture_output=True, timeout=TIMEOUT_S,
                              env=env)
    except subprocess.TimeoutExpired:
        raise ResolverError("bitwarden resolver: `bw` timed out")
    except OSError as e:
        raise ResolverError(f"bitwarden resolver: failed to run bw: {e}")
    if proc.returncode != 0:
        err = safe_stderr(proc.stderr)
        raise ResolverError(
            f"bitwarden resolver: `bw` failed (rc={proc.returncode}): {err}")
    return proc.stdout.decode("utf-8", "replace")


def resolve(ref, config):
    binary = shutil.which("bw")
    if not binary:
        raise ResolverError("bitwarden resolver: `bw` CLI not found on PATH")
    item, field = plan(ref, config)

    if field in DIRECT_FIELDS:
        out = _run([binary, "get", field, item], config)
        return out[:-1] if out.endswith("\n") else out

    # Custom field: pull the whole item and read fields[].name == field.
    out = _run([binary, "get", "item", item], config)
    try:
        obj = json.loads(out)
    except json.JSONDecodeError:
        raise ResolverError("bitwarden resolver: could not parse `bw get item` JSON")
    for f in (obj.get("fields") or []):
        if f.get("name") == field:
            return str(f.get("value") or "")
    raise ResolverError(
        f"bitwarden resolver: no field {field!r} on item {item!r}")

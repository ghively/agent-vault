#!/usr/bin/env python3
"""
resolvers.op — resolve secrets from 1Password via the `op` CLI.

Config (registry/resolvers.yaml):

    onepassword:
      module: resolvers.op
      # account: my.1password.com     # optional: passed as `op --account`
      # default_field: password       # field used when the ref omits one

Ref → 1Password secret-reference mapping:

    onepassword://Private/bofa-login            ->  op://Private/bofa-login/password
    onepassword://Private/bofa-login/credential ->  op://Private/bofa-login/credential

i.e. the ref is `onepassword://<vault>/<item>[/<field>]`; with no field it uses
`default_field` (default `password`). Resolution shells out to `op read`, so the
plaintext never transits a Python age/secret library and is never written down.

AUTHENTICATION is the operator's responsibility and is intentionally NOT handled
here: run `op signin` first, or set OP_SERVICE_ACCOUNT_TOKEN in the environment.
This resolver simply invokes the already-authenticated CLI.
"""
import shutil
import subprocess
from . import ResolverError, safe_stderr

TIMEOUT_S = 30


def op_uri(ref, config=None):
    """Map a parsed ref to a 1Password `op://vault/item/field` URI (pure)."""
    segments = [ref.store, *ref.path]
    if len(segments) < 2:
        raise ResolverError(
            "1password resolver: ref must be onepassword://<vault>/<item>"
            "[/<field>] (need at least a vault and an item)")
    if len(segments) == 2:
        field = (config or {}).get("default_field") or "password"
        segments = [*segments, field]
    return "op://" + "/".join(segments)


def resolve(ref, config):
    binary = shutil.which("op")
    if not binary:
        raise ResolverError("1password resolver: `op` CLI not found on PATH")
    uri = op_uri(ref, config)
    args = [binary, "read", "--no-newline", uri]
    account = (config or {}).get("account")
    if account:
        args += ["--account", str(account)]
    try:
        proc = subprocess.run(args, capture_output=True, timeout=TIMEOUT_S)
    except subprocess.TimeoutExpired:
        raise ResolverError("1password resolver: `op read` timed out")
    except OSError as e:
        raise ResolverError(f"1password resolver: failed to run op: {e}")
    if proc.returncode != 0:
        err = safe_stderr(proc.stderr)
        raise ResolverError(
            f"1password resolver: `op read` failed (rc={proc.returncode}): {err}")
    try:
        secret = proc.stdout.decode("utf-8")
    except UnicodeDecodeError:
        raise ResolverError("1password resolver: secret is not UTF-8 text")
    return secret[:-1] if secret.endswith("\n") else secret

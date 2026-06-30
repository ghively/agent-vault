#!/usr/bin/env python3
"""
resolvers.gpg_file — decrypt gpg-encrypted secret files on the NAS.

Config (registry/resolvers.yaml):

    gpg:
      module: resolvers.gpg_file
      store_dir: /volume1/secrets             # where the .gpg files live
      # passphrase_env: GPG_PASSPHRASE        # optional: symmetric-cipher passphrase
                                              # from an env var (loopback pinentry).
                                              # Omit for asymmetric keys via gpg-agent.

Ref → file mapping (mirrors the age backend):

    gpg://infra/synology-ssh   ->   <store_dir>/infra/synology-ssh.gpg

Decryption shells out to the `gpg` binary. With no `passphrase_env`, decryption
relies on gpg-agent (asymmetric keys). With `passphrase_env`, the named env var
supplies a symmetric passphrase via loopback pinentry. The plaintext is returned
to the caller and never written anywhere; store_dir containment is enforced.
"""
import os, shutil, subprocess
from . import ResolverError, safe_stderr

TIMEOUT_S = 30


def encrypted_path(ref, store_dir):
    """Build and containment-check the .gpg path for a ref (pure-ish: filesystem
    realpath only). Raises if the ref escapes store_dir."""
    segments = [ref.store, *ref.path]
    enc = os.path.join(store_dir, *segments) + ".gpg"
    real_store = os.path.realpath(store_dir)
    real_enc = os.path.realpath(enc)
    if real_enc != real_store and not real_enc.startswith(real_store + os.sep):
        raise ResolverError(f"gpg resolver: ref {ref.raw!r} escapes store_dir; refusing")
    return enc


def resolve(ref, config):
    store_dir = os.path.expanduser((config or {}).get("store_dir") or "")
    if not store_dir:
        raise ResolverError("gpg resolver: store_dir not configured")
    enc = encrypted_path(ref, store_dir)
    if not os.path.isfile(enc):
        raise ResolverError(f"gpg resolver: no encrypted secret for {ref.raw!r} (expected {enc})")
    binary = shutil.which("gpg") or shutil.which("gpg2")
    if not binary:
        raise ResolverError("gpg resolver: `gpg` binary not found on PATH")

    args = [binary, "--decrypt", "--quiet", "--batch"]
    stdin_bytes = None
    pass_env = (config or {}).get("passphrase_env")
    if pass_env:
        secret_pass = os.environ.get(pass_env)
        if not secret_pass:
            raise ResolverError(f"gpg resolver: passphrase_env {pass_env!r} is not set")
        # Pass the passphrase on a pipe, NOT on argv (argv is world-readable via
        # /proc/<pid>/cmdline on a multi-user host).
        args += ["--pinentry-mode", "loopback", "--passphrase-fd", "0"]
        stdin_bytes = secret_pass.encode()
    args.append(enc)

    try:
        proc = subprocess.run(args, input=stdin_bytes, capture_output=True,
                              timeout=TIMEOUT_S)
    except subprocess.TimeoutExpired:
        raise ResolverError("gpg resolver: decryption timed out")
    except OSError as e:
        raise ResolverError(f"gpg resolver: failed to run gpg: {e}")
    if proc.returncode != 0:
        err = safe_stderr(proc.stderr)
        raise ResolverError(f"gpg resolver: decryption failed (rc={proc.returncode}): {err}")
    try:
        secret = proc.stdout.decode("utf-8")
    except UnicodeDecodeError:
        raise ResolverError("gpg resolver: decrypted content is not UTF-8 text")
    return secret[:-1] if secret.endswith("\n") else secret

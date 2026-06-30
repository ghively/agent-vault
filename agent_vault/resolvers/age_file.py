#!/usr/bin/env python3
"""
resolvers.age_file — decrypt age-encrypted secrets stored on the NAS.

Config (registry/resolvers.yaml):

    age:
      module: resolvers.age_file
      keyfile: ~/.config/agentvault/age.key     # private identity, NOT in the vault
      store_dir: /volume1/secrets            # where the .age files live

Ref → file mapping:

    age://infra/synology-ssh   ->   <store_dir>/infra/synology-ssh.age
    age://banking/bofa/login   ->   <store_dir>/banking/bofa/login.age

Decryption shells out to the `age` (or `rage`) binary — no Python age library
dependency, matching the lowest-dependency / harness-independent ethos. The
private key never leaves the keyfile; the plaintext is returned to the caller
and never written anywhere.
"""
import os
import shutil
import subprocess
from . import ResolverError, safe_stderr

DECRYPT_TIMEOUT_S = 30


def resolve(ref, config):
    keyfile = os.path.expanduser((config or {}).get("keyfile") or "")
    store_dir = os.path.expanduser((config or {}).get("store_dir") or "")
    if not store_dir:
        raise ResolverError("age resolver: store_dir not configured")
    if not keyfile:
        raise ResolverError("age resolver: keyfile not configured")
    if not os.path.isfile(keyfile):
        raise ResolverError(f"age resolver: keyfile not found at {keyfile}")

    # Build the encrypted-file path, then verify it stays inside store_dir.
    # parse_ref already rejected '.'/'..' segments; the realpath containment
    # check is the belt to that suspenders (handles symlinks too).
    segments = [ref.store, *ref.path]
    enc = os.path.join(store_dir, *segments) + ".age"
    real_store = os.path.realpath(store_dir)
    real_enc = os.path.realpath(enc)
    if real_enc != real_store and not real_enc.startswith(real_store + os.sep):
        raise ResolverError(
            f"age resolver: ref {ref.raw!r} escapes store_dir; refusing")
    if not os.path.isfile(enc):
        raise ResolverError(
            f"age resolver: no encrypted secret for {ref.raw!r} "
            f"(expected {enc})")

    binary = shutil.which("age") or shutil.which("rage")
    if not binary:
        raise ResolverError(
            "age resolver: neither 'age' nor 'rage' binary found on PATH")

    try:
        proc = subprocess.run(
            [binary, "--decrypt", "--identity", keyfile, enc],
            capture_output=True, timeout=DECRYPT_TIMEOUT_S)
    except subprocess.TimeoutExpired:
        raise ResolverError("age resolver: decryption timed out")
    except OSError as e:
        raise ResolverError(f"age resolver: failed to run {binary}: {e}")

    if proc.returncode != 0:
        err = safe_stderr(proc.stderr)
        raise ResolverError(
            f"age resolver: decryption failed (rc={proc.returncode}): {err}")

    try:
        secret = proc.stdout.decode("utf-8")
    except UnicodeDecodeError:
        raise ResolverError(
            "age resolver: decrypted content is not UTF-8 text "
            "(binary secrets are not supported by the text resolver)")
    # Secret files conventionally carry a trailing newline; strip exactly one
    # so `synapse resolve x | something` doesn't pass a stray \n.
    return secret[:-1] if secret.endswith("\n") else secret
